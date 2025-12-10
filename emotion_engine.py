# WebSocket server bridging Unity, logger.py, agent.py, and emotion_engine.py.

import asyncio
import json
from datetime import datetime
import os
import sys
import subprocess

import websockets
from websockets.server import WebSocketServerProtocol
import logger
import agent

# Paths and global state
ENV_STATE_FILE = os.path.join(os.path.dirname(__file__), "env_state.json")
TRIGGER_FILE = os.path.join(os.path.dirname(__file__), "face_trigger.txt")
TIME_RESET_FILE = os.path.join(os.path.dirname(__file__), "time_reset.txt")

face_process = None
dialogue_concession = 0.0
conversation_history: list[dict] = []
CONVERSATION_LOG_PATH = "conversation_log.jsonl"
connected_clients: set[WebSocketServerProtocol] = set()
current_item_info = None


class EnvironmentState:
    """Holds current environment parameters."""

    def __init__(self):
        self.noise_level = 0
        self.crowd_density = 0
        self.lighting_level = 0
        self.visual_distractions = 0
        self.time_pressure = 0  # controlled only by backend

    def update_from_dict(self, data: dict):
        if "noise_level" in data:
            self.noise_level = int(max(0, min(10, data["noise_level"])))
        if "crowd_density" in data:
            self.crowd_density = int(max(0, min(10, data["crowd_density"])))
        if "lighting_level" in data:
            self.lighting_level = int(max(0, min(10, data["lighting_level"])))
        if "visual_distractions" in data:
            self.visual_distractions = int(
                max(0, min(10, data["visual_distractions"]))
            )

    def to_dict(self) -> dict:
        return {
            "noise_level": self.noise_level,
            "crowd_density": self.crowd_density,
            "lighting_level": self.lighting_level,
            "visual_distractions": self.visual_distractions,
            "time_pressure": self.time_pressure,
        }

    def pretty_print(self):
        print("========== Environment State ==========")
        print(f"Noise level:           {self.noise_level}/10")
        print(f"Crowd density:         {self.crowd_density}/10")
        print(f"Lighting level:        {self.lighting_level}/10")
        print(f"Visual distractions:   {self.visual_distractions}/10")
        print(f"Time pressure:         {self.time_pressure}/10")
        print("=======================================")


env_state = EnvironmentState()


def start_face_detection_if_needed():
    """Start emotion_engine.py as a subprocess if not already running."""
    global face_process

    if face_process is not None and face_process.poll() is None:
        return

    face_script = os.path.join(os.path.dirname(__file__), "emotion_engine.py")

    if not os.path.exists(face_script):
        print(f"[Face] emotion_engine.py not found, path: {face_script}")
        return

    try:
        print(f"[Face] Starting emotion engine process: {face_script}")
        face_process = subprocess.Popen([sys.executable, face_script])
    except Exception as e:
        print("[Face] Failed to start emotion_engine.py:", e)


def append_history(utterance: str):
    """Append current utterance and environment snapshot to in-memory and jsonl log."""
    record = {
        "index": len(conversation_history),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "utterance": utterance,
        "environment": env_state.to_dict(),
    }

    conversation_history.append(record)

    try:
        with open(CONVERSATION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[History] Failed to write conversation_log.jsonl:", e)

    print("\n[History] Saved one turn:")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def trigger_face_statistics():
    """Write a trigger file to notify emotion_engine.py to compute statistics."""
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
        print("[FaceTrigger] Trigger file written.")
    except Exception as e:
        print("[FaceTrigger] Failed to write trigger file:", e)


async def handle_env_update(data: dict, websocket: WebSocketServerProtocol):
    env_state.update_from_dict(data)
    print("\n[Env] Received environment update:")
    env_state.pretty_print()

    try:
        with open(ENV_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(env_state.to_dict(), f, ensure_ascii=False, indent=2)
        print("[Env] env_state.json updated.")
    except Exception as e:
        print("[Env] Failed to write env_state.json:", e)

    ack = {
        "type": "env_received",
        "status": "ok",
        "env": env_state.to_dict(),
    }
    await websocket.send(json.dumps(ack, ensure_ascii=False))


async def handle_user_utterance(data: dict, websocket: WebSocketServerProtocol):
    global dialogue_concession, current_item_info

    utterance = (data.get("utterance") or "").strip()
    if not utterance:
        print("[Utterance] Empty utterance, ignored.")
        return

    print("\n[Utterance] User:", utterance)

    record = None
    try:
        record = logger.log_turn(
            utterance=utterance,
            env_state=env_state.to_dict(),
            item_info=current_item_info,
        )
    except Exception as e:
        print("[Server] logger.log_turn failed:", e)

    append_history(utterance)
    trigger_face_statistics()
    start_face_detection_if_needed()

    dialogue_concession = min(dialogue_concession + 5.0, 50.0)
    print(f"[Concession] Dialogue-based concession: {dialogue_concession:.1f}%")

    agent_reply = None
    if isinstance(record, dict):
        try:
            agent_reply = agent.call_chatgpt_with_record(record)
            print("\n[AgentReply] Generated seller reply:")
            print(agent_reply)
        except Exception as e:
            print("[Server] agent.call_chatgpt_with_record failed:", e)
    else:
        print("[Server] No valid record, skip agent reply.")

    ack = {
        "type": "utterance_received",
        "status": "ok",
        "echo": utterance,
        "dialogue_concession": dialogue_concession,
    }
    if agent_reply is not None:
        ack["agent_reply"] = agent_reply

    await websocket.send(json.dumps(ack, ensure_ascii=False))


async def handle_item_selected(data: dict, websocket: WebSocketServerProtocol):
    """Handle item selection message from Unity."""
    global current_item_info

    current_item_info = {
        "item_id": str(data.get("itemId") or data.get("item_id") or ""),
        "item_name": (data.get("itemName") or data.get("item_name") or "").strip(),
        "max_price": float(data.get("maxPrice") or data.get("max_price") or 0),
        "min_price": float(data.get("MinPrice") or data.get("min_price") or 0),
    }

    print("\n[Item] Received item info:")
    print(json.dumps(current_item_info, ensure_ascii=False, indent=2))

    try:
        with open(TIME_RESET_FILE, "w", encoding="utf-8") as f:
            f.write(
                f"reset at {datetime.now().isoformat()} "
                f"for item {current_item_info['item_id']}"
            )
        print("[Server] time_reset.txt written, reset time_pressure_counter.")
    except Exception as e:
        print("[Server] Failed to write time_reset.txt:", e)

    try:
        logger.reset_history_for_new_item()
    except Exception as e:
        print("[Server] logger.reset_history_for_new_item failed:", e)

    try:
        logger.log_item_update(
            item_info=current_item_info,
            env_state=env_state.to_dict(),
        )
    except Exception as e:
        print("[Server] logger.log_item_update failed:", e)

    ack = {
        "type": "item_received",
        "status": "ok",
        "item": current_item_info,
    }
    await websocket.send(json.dumps(ack, ensure_ascii=False))


async def process_message(message: str, websocket: WebSocketServerProtocol):
    """Dispatch messages based on the 'type' field."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        print("[Server] Non-JSON message received:", message)
        return

    msg_type = data.get("type")

    if msg_type == "env_update":
        await handle_env_update(data, websocket)
    elif msg_type == "user_utterance":
        await handle_user_utterance(data, websocket)
    elif msg_type == "item_selected":
        await handle_item_selected(data, websocket)
    else:
        print(f"[Server] Unknown message type={msg_type}, data={data}")


async def client_handler(websocket: WebSocketServerProtocol):
    """Handle one client connection."""
    connected_clients.add(websocket)
    client_addr = websocket.remote_address
    print(f"[Server] Client connected: {client_addr}, total={len(connected_clients)}")

    start_face_detection_if_needed()

    try:
        async for message in websocket:
            await process_message(message, websocket)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        print(f"[Server] Client disconnected: {client_addr}, total={len(connected_clients)}")

        try:
            logger.reset_history_max_concession()
        except Exception as e:
            print("[Server] logger.reset_history_max_concession failed:", e)


async def main():
    host = "127.0.0.1"
    port = 5200

    print(f"[Server] Starting WebSocket server at ws://{host}:{port}")
    async with websockets.serve(client_handler, host, port):
        print("[Server] Server started, waiting for clients...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Server] Keyboard interrupt, shutting down.")
    finally:
        if "face_process" in globals() and face_process is not None:
            if face_process.poll() is None:
                print("[Face] Terminating emotion engine process...")
                face_process.terminate()
