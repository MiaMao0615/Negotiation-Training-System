# -*- coding: utf-8 -*-
"""
Agent client:
- Build a compact payload from one negotiation log record.
- Call the OpenAI-compatible chat API to generate seller replies.
"""

from typing import Any, Dict, List
import os
from openai import OpenAI

# ===== 1. Initialize OpenAI client and show basic env state =====
api_key = os.environ.get("OPENAI_API_KEY")
base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

print("[AGENT] OPENAI_API_KEY found:", api_key is not None)
print("[AGENT] OPENAI_BASE_URL:", base_url)

client = OpenAI(
    base_url=base_url,
    api_key=api_key,
)


# ===== 2. Convert one log record into a compact payload for the LLM =====
def build_chatgpt_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract key fields from a single negotiation_log record.

    record comes from logger.log_turn():
    {
        "timestamp": ...,
        "utterance": ...,
        "history": [...],
        "environment": {...},
        "item_info": {...} or None,
        "face_result": {...} or None,
        "final_concession": float,
        "history_max_concession": float,
        "concession_amount": float,
        "suggested_price": float
    }
    """

    # Item info
    item_info = record.get("item_info") or {}
    item_name = item_info.get("item_name", "这个东西")
    max_price = item_info.get("max_price", None)
    min_price = item_info.get("min_price", None)

    # Face/emotion + strategy info
    face_result = record.get("face_result") or {}
    emotion_cn = face_result.get("cn_emotion", "中立")
    strategy = face_result.get("strategy", "理性分析支持")
    strategy_detail = face_result.get("strategy_detail", "")
    language_style = face_result.get("language_style", "")

    # Price info
    suggested_price = record.get("suggested_price", None)

    # Dialogue info
    utterance = record.get("utterance", "")
    history = record.get("history", []) or []

    payload: Dict[str, Any] = {
        "item_name": item_name,
        "max_price": max_price,
        "min_price": min_price,
        "emotion_cn": emotion_cn,
        "strategy": strategy,
        "strategy_detail": strategy_detail,
        "language_style": language_style,
        "suggested_price": suggested_price,
        "user_utterance": utterance,
        "history": history,
    }
    return payload


# ===== 3. Main entry: generate seller reply from one record =====
def call_chatgpt_with_record(record: Dict[str, Any]) -> str:
    """
    Call the OpenAI-compatible chat.completions API to generate a seller reply.
    The reply must respect record['suggested_price'] as the final price anchor.
    """
    payload = build_chatgpt_payload(record)
    print("[AGENT_DEBUG] record keys:", list(record.keys()))
    print("[AGENT_DEBUG] record['suggested_price'] =", record.get("suggested_price"))

    item_name = payload.get("item_name", "这个东西")
    suggested_price = payload.get("suggested_price", None)
    emotion_cn = payload.get("emotion_cn", "中立")
    strategy = payload.get("strategy", "理性分析支持")
    strategy_detail = payload.get("strategy_detail", "")
    language_style = payload.get("language_style", "")
    user_utterance = payload.get("user_utterance", "")
    history: List[str] = payload.get("history", [])

    if history:
        history_text = " / ".join(history)
    else:
        history_text = "（这是买家的第一句话）"

    if not isinstance(suggested_price, (int, float)):
        suggested_price = 0.0

    # Simple debug log before calling the model
    print(
        f"[AGENT] Calling ChatGPT, item={item_name}, "
        f"suggested_price={suggested_price:.2f}, emotion={emotion_cn}, strategy={strategy}"
    )

    system_msg = {
        "role": "system",
        "content": (
            "你是一位在夜市摆摊的小商贩，需要和顾客讨价还价。"
            "系统已经给出【建议价格】P_sys，作为本轮你的参考成交价。\n\n"
            "【价格决策硬规则】\n"
            "1. 先从买家的话中识别他给出的最高明确出价 P_user_max（例如“15块可以吗”“20元行不行”）。\n"
            "2. 如果存在 P_user_max 且 P_user_max ≥ P_sys：\n"
            "   - 必须立刻按 P_user_max 成交，不再还价，也不能报出任何其他数字；\n"
            "   - 明确说出这个数字，例如“那就按 18 元给你吧”；\n"
            "   - 语气可以稍微表现出被砍狠了、对方赚到大便宜，但态度是确实同意成交。\n"
        ),
    }

    user_msg = {
        "role": "user",
        "content": (
            f"【商品】{item_name}\n"
            f"【建议价格 P_sys】{suggested_price:.2f} 元\n\n"
            f"【表情与策略】\n"
            f"- 主表情：{emotion_cn}\n"
            f"- 策略：{strategy}\n"
            f"- 策略说明：{strategy_detail}\n"
            f"- 推荐语言风格：{language_style}\n\n"
            f"【历史对话（买家视角，时间从早到晚）】\n"
            f"{history_text}\n\n"
            f"【买家本轮发言】\n"
            f"买家：{user_utterance}\n\n"
            f"请你扮演摊主，用自然、口语化的英文回复买家，并严格执行以下价格逻辑：\n"
            f"1. 如果本轮发言中存在明确出价 P_user，且 P_user ≥ P_sys（{suggested_price:.2f} 元），"
            f"   你必须直接按 P_user 成交，只报这一种价格，不再还价。\n"
            f"2. 如果没有明确出价，或者 P_user < P_sys，则用 P_sys（{suggested_price:.2f} 元）作为唯一报价，"
            f"   比如“最低给你 {suggested_price:.2f} 元了”。\n"
        ),
    }

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini-2024-07-18",
            messages=[system_msg, user_msg],
            temperature=0.7,
            max_tokens=256,
        )
        reply_text = completion.choices[0].message.content

        print("[AGENT] ChatGPT call succeeded. Reply:")
        print(reply_text)
        print("-" * 80)

        return reply_text

    except Exception as e:
        print("[AGENT] ChatGPT call failed:", e)
        # Fallback text (keep original behavior)
        return "（抱歉，系统暂时没算出合适的价格，请稍后再试。）"
