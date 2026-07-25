"""Product FAQ / don't-know helpers for Valet learning loop."""

from __future__ import annotations

import re
from typing import Any

DONT_KNOW_MARKER = "[[DONT_KNOW]]"

DONT_KNOW_REPLY = (
    "Для ответа на этот вопрос мне нужно время — отвечу в течение 24 часов.\n"
    "Пока можем продолжить беседу по другим вопросам."
)

DONT_KNOW_PHRASES = (
    "не знаю",
    "не уверен",
    "нет информации",
    "не могу сказать точно",
    "у меня нет данных",
    "не умею",
    "нет такой функции",
    "не имею доступа",
)

# Invented product claims that should never be trusted
INVENTED_PHRASES = (
    "интеграц",
    "подключ",
    "настройках приложения",
    "привязывать счет",
    "привязать банк",
    "привязывать счёт",
    "автоматический учет",
    "автоматический учёт",
)

HOWTO_HINTS = (
    "как ",
    "каким образом",
    "где найти",
    "где кнопка",
    "как добавить",
    "как загрузить",
    "как импорт",
    "как удалить",
    "можно ли",
    "умеешь ли",
    "поддерживаешь",
)

# If the model already points to real Wallet flows, do not escalate to «не знаю»
GROUNDED_HOWTO_SIGNALS = (
    ("разбор", "csv"),
    ("разбор", "выписк"),
    ("загруз", "csv"),
    ("импорт", "csv"),
)


def format_knowledge_block(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(шпаргалка пуста)"
    lines = []
    for r in rows:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        if not title and not body:
            continue
        lines.append(f"• {title}: {body}" if title else f"• {body}")
    return "\n".join(lines) if lines else "(шпаргалка пуста)"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", (text or "").lower()) if t}


def match_knowledge_article(
    question: str,
    rows: list[dict[str, Any]],
    *,
    min_score: int = 2,
) -> dict[str, Any] | None:
    """Simple keyword match against active knowledge articles."""
    q = _tokens(question)
    if not q or not rows:
        return None
    best = None
    best_score = 0
    for r in rows:
        if r.get("is_active") is False:
            continue
        blob = f"{r.get('title') or ''} {r.get('body') or ''}"
        score = len(q & _tokens(blob))
        # Boost obvious FAQ topics
        title = (r.get("title") or "").lower()
        body = (r.get("body") or "").lower()
        ql = (question or "").lower()
        if "выписк" in ql or "csv" in ql or "импорт" in ql:
            if any(x in title or x in body for x in ("выписк", "csv", "импорт", "разбор")):
                score += 3
        if "расход" in ql and ("расход" in title or "расход" in body):
            score += 2
        if score > best_score:
            best_score = score
            best = r
    if best and best_score >= min_score:
        return best
    return None


def format_article_reply(article: dict[str, Any]) -> str:
    title = (article.get("title") or "").strip()
    body = (article.get("body") or "").strip()
    if title and body:
        return f"{body}"
    return body or title


def reply_looks_grounded(reply: str) -> bool:
    text = (reply or "").lower()
    return any(a in text and b in text for a, b in GROUNDED_HOWTO_SIGNALS)


def strip_dont_know_marker(text: str) -> tuple[str, bool]:
    raw = (text or "").strip()
    if not raw:
        return "", False
    if raw.startswith(DONT_KNOW_MARKER):
        rest = raw[len(DONT_KNOW_MARKER) :].lstrip(" \n:-—")
        return rest, True
    if DONT_KNOW_MARKER in raw:
        cleaned = raw.replace(DONT_KNOW_MARKER, "").strip()
        return cleaned, True
    return raw, False


def heuristic_dont_know(reply: str, user_message: str = "") -> bool:
    text = (reply or "").lower()
    um = (user_message or "").lower()
    if not text:
        return False
    if reply_looks_grounded(text):
        # Still catch pure inventions even if somehow mixed with CSV mention
        if any(x in text for x in INVENTED_PHRASES) and any(h in um for h in HOWTO_HINTS):
            return True
        return False
    invented = any(x in text for x in INVENTED_PHRASES)
    howto = any(h in um for h in HOWTO_HINTS)
    if invented and howto:
        return True
    if any(p in text for p in DONT_KNOW_PHRASES) and howto:
        return True
    if any(
        x in text
        for x in (
            "не могу напрямую",
            "нет доступа к функциям",
            "нет функции",
        )
    ) and howto:
        return True
    return False


def detect_dont_know(reply: str, *, user_message: str = "", greeting: bool = False) -> tuple[str, bool]:
    if greeting:
        cleaned, _ = strip_dont_know_marker(reply)
        return cleaned or reply, False
    cleaned, marked = strip_dont_know_marker(reply)
    if marked and reply_looks_grounded(cleaned):
        return cleaned, False
    if marked:
        return cleaned, True
    if heuristic_dont_know(cleaned or reply, user_message):
        return cleaned or reply, True
    return cleaned or reply, False


def resolve_product_answer(
    *,
    user_message: str,
    model_reply: str,
    knowledge_rows: list[dict[str, Any]],
    greeting: bool = False,
) -> dict[str, Any]:
    """
    Decide final user-facing reply for product/how-to questions.
    Prefer knowledge hit over deferred «не знаю» when FAQ already covers the topic.
    """
    cleaned, dont_know = detect_dont_know(model_reply, user_message=user_message, greeting=greeting)
    article = match_knowledge_article(user_message, knowledge_rows)
    if article and (dont_know or any(h in (user_message or "").lower() for h in HOWTO_HINTS)):
        # If model invented UI but we have FAQ — answer from FAQ
        if dont_know or any(x in (cleaned or "").lower() for x in INVENTED_PHRASES):
            return {
                "reply": format_article_reply(article),
                "dont_know": False,
                "from_knowledge": True,
                "knowledge_id": article.get("id"),
            }
    if article and dont_know:
        return {
            "reply": format_article_reply(article),
            "dont_know": False,
            "from_knowledge": True,
            "knowledge_id": article.get("id"),
        }
    return {
        "reply": cleaned or model_reply,
        "dont_know": bool(dont_know),
        "from_knowledge": False,
        "knowledge_id": None,
    }


def deferred_answer_text(question: str, answer: str) -> str:
    q = (question or "").strip()
    a = (answer or "").strip()
    return (
        "Здравствуйте, ранее вы обращались к нам с вопросом:\n"
        f"«{q}»\n\n"
        "Получен следующий ответ:\n"
        f"{a}"
    )


def satisfaction_thanks() -> str:
    return (
        "Спасибо за ожидание. Мы здесь, чтобы помогать 24/7 — "
        "спрашивайте, если появится ещё что-то по плану или приложению."
    )


def is_overdue(created_at, hours: int = 24) -> bool:
    if created_at is None:
        return False
    try:
        from datetime import datetime, timezone

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - created_at).total_seconds() > hours * 3600
    except Exception:  # noqa: BLE001
        return False
