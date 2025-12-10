# -*- coding: utf-8 -*-
"""
Central logging module for the negotiation system:
- Saves face/environment statistics.
- Logs each turn (utterance + env + item + face stats + price suggestion).
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

# Base directory
BASE_DIR = os.path.dirname(__file__)

# Paths
FACE_RESULT_PATH = os.path.join(BASE_DIR, "negotiation_result.json")
TURN_LOG_PATH = os.path.join(BASE_DIR, "negotiation_log.jsonl")
META_PATH = os.path.join(BASE_DIR, "negotiation_meta.json")

# In-memory conversation history (for the current server process)
_conversation_history: List[str] = []

# In-memory cache of global max concession across turns
_history_max_concession: Optional[float] = None


# ===== 1. For emotion_engine.py: save / load face + env statistics =====

def save_face_result(result: Dict[str, Any]) -> None:
    """
    Called by emotion_engine.py.

    Example result:
    {
        "timestamp": "...",
        "frames_total": 90,
        "primary_expression": "Neutral",
        "primary_percentage": 97.8,
        "base_concession": -0.5,
        "env_impacts": { ... },
        "env_avg": 4.2,
        "time_impact": 15.0,
        "combined_env": 19.2,
        "final_concession": 18.7
    }
    """
    try:
        with open(FACE_RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[Doc] Face result written to {FACE_RESULT_PATH}")
    except Exception as e:
        print("[Doc] Failed to write face result:", e)


def load_face_result() -> Optional[Dict[str, Any]]:
    """Load the latest face statistics for the server (may return None)."""
    try:
        with open(FACE_RESULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        print("[Doc] Failed to read face result:", e)
        return None


# ===== 2. Helpers for global max concession =====

def _load_history_max_from_file() -> Optional[float]:
    """Load history_max_concession from META_PATH into memory if needed."""
    global _history_max_concession
    if _history_max_concession is not None:
        return _history_max_concession

    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        val = data.get("history_max_concession", None)
        if isinstance(val, (int, float)):
            _history_max_concession = float(val)
        else:
            _history_max_concession = None
    except FileNotFoundError:
        _history_max_concession = None
    except Exception as e:
        print("[Doc] Failed to load history_max_concession:", e)
        _history_max_concession = None

    return _history_max_concession


def _save_history_max_to_file() -> None:
    """Write the current _history_max_concession to META_PATH."""
    try:
        data = {"history_max_concession": _history_max_concession}
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[Doc] Failed to write history_max_concession:", e)


def reset_history_max_concession() -> None:
    """Reset history_max_concession to 0.0 (called when Unity disconnects)."""
    global _history_max_concession
    _history_max_concession = 0.0
    try:
        _save_history_max_to_file()
        print("[Doc] history_max_concession reset to 0.0")
    except Exception as e:
        print("[Doc] Failed to reset history_max_concession:", e)


def log_item_update(
    item_info: Dict[str, Any],
    env_state: Dict[str, Any],
) -> None:
    """
    Called when a new item is selected/changed.
    Append an item_update event into negotiation_log.jsonl.
    """
    record: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": "item_update",
        "item_info": item_info,
        "environment": env_state,
    }

    # Append to JSONL log
    try:
        with open(TURN_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception as e:
        print("[Doc] Failed to write item_update log:", e)
        return

    print("[Doc] Item update logged.")


def reset_history_for_new_item() -> None:
    """
    Called when selecting a new item:
    - Reset history_max_concession to 0.0.
    - Clear current conversation history.
    """
    global _history_max_concession, _conversation_history

    _conversation_history = []
    _history_max_concession = 0.0
    try:
        _save_history_max_to_file()
        print("[Doc] New item selected: history_max_concession reset to 0.0")
    except Exception as e:
        print("[Doc] Failed to reset history_max_concession for new item:", e)


# ===== 3. For server.py: log each Unity utterance =====

def log_turn(
    utterance: str,
    env_state: Dict[str, Any],
    item_info: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Called on each Unity user_utterance.

    Writes one line into negotiation_log.jsonl with:
      - utterance / history / environment / item_info
      - face_result / final_concession
      - history_max_concession
      - concession_amount
      - suggested_price
    """
    global _conversation_history, _history_max_concession

    text = (utterance or "").strip()
    if not text:
        return

    # 1) Update history
    _conversation_history.append(text)

    # 2) Load latest face statistics (may be None)
    face_result = load_face_result()
    final_concession = None
    if isinstance(face_result, dict):
        final_concession = face_result.get("final_concession", None)

    # 3) Update global max concession using final_concession
    _load_history_max_from_file()
    if isinstance(final_concession, (int, float)):
        if (_history_max_concession is None) or (final_concession > _history_max_concession):
            _history_max_concession = float(final_concession)
            _save_history_max_to_file()

    # 4) Compute concession_amount and suggested_price if possible
    concession_amount = None
    suggested_price = None

    if item_info:
        try:
            max_price = float(item_info.get("max_price", 0))
            min_price = float(item_info.get("min_price", 0))

            gap = max_price - min_price
            if gap < 0:
                gap = 0.0

            p = float(_history_max_concession)

            concession_amount = gap * (p / 100.0)
            suggested_price = max_price - concession_amount

            if suggested_price < min_price:
                suggested_price = min_price

        except Exception as e:
            print("[Doc] Failed to compute suggested price:", e)
            concession_amount = None
            suggested_price = None

    # 5) Build record and write JSONL
    record: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "utterance": text,
        "history": _conversation_history.copy(),
        "environment": env_state,
        "item_info": item_info,
        "face_result": face_result,
        "final_concession": final_concession,
        "history_max_concession": _history_max_concession,
        "concession_amount": concession_amount,
        "suggested_price": suggested_price,
    }

    try:
        with open(TURN_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception as e:
        print("[Doc] Failed to write turn log:", e)

    # Short debug message only
    print("[Doc] Turn logged.")
    return record
