"""LLM client for Valet — Timeweb AI agent (primary) + OpenRouter (optional)."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx


SYSTEM_PHASE0 = """Ты — Валет, навигатор плана в приложении Wallet пользователя Макс.
North Star: помочь ответить — идут ли расходы по плану, или опережают возможности?

Правила:
1. Обращайся «Макс» на «ты». Кратко, по делу. Без морали о личной жизни.
2. Любые цифры — только из блока «сводка плана» ниже. Не выдумывай цифры.
3. Как пользоваться приложением — только из блока «шпаргалка продукта». Не выдумывай кнопки, экраны, интеграции с банком, PDF/OCR, голосовой ввод и т.п.
4. Структура разбора: цифры → объяснение → (если уместно) один практичный совет.
5. Сейчас у тебя нет права менять данные. Не обещай записать расход и не принимай файлы.
6. Не лицензированный финсоветник.
7. Если просят «кратко» — только доклад, без советов.
8. Про темп говори в процентах, не «п.п.».
9. «Особый контроль» (!) — темп как есть (плюс или минус). «Предупреждения» — только авто-перерасход, без !.
10. Касса = счета. Свободные деньги = касса − денежный запас. Не путай с конвертом плана «база/запас».
11. Если ответа нет ни в сводке плана, ни в шпаргалке продукта — НЕ выдумывай. Ответь одной строкой, начиная с маркера [[DONT_KNOW]] (можно коротко уточнить тему после маркера).
12. Формат ответа: только обычный русский текст. Без JSON, YAML, кодблоков, технических ключей (кроме маркера [[DONT_KNOW]]).
Отвечай по-русски."""


def _rub(n: Any) -> str:
    try:
        v = int(round(float(n or 0)))
    except (TypeError, ValueError):
        v = 0
    return f"{v:,}".replace(",", " ") + " ₽"


def _watch_title(row: dict[str, Any]) -> str:
    cat = row.get("category") or "?"
    exp = row.get("expense")
    return f"{cat} — {exp}" if exp else str(cat)


def _fmt_watch_line(row: dict[str, Any]) -> str:
    title = _watch_title(row)
    delta = int(row.get("delta_vs_pace") or 0)
    label = row.get("delta_label") or ("запас" if delta > 0 else ("перерасход" if delta < 0 else "по темпу"))
    pct = row.get("pct_of_pace")
    pct_bit = f", {pct}% к темпу" if pct is not None else ""
    if delta > 0:
        return f"{title}: {label} +{abs(delta)} ₽{pct_bit}"
    if delta < 0:
        return f"{title}: {label} −{abs(delta)} ₽{pct_bit}"
    return f"{title}: {label}{pct_bit}"


def _fmt_alert_line(row: dict[str, Any]) -> str:
    title = _watch_title(row)
    rub = int(row.get("overspend_rub") or 0)
    if row.get("unplanned") or row.get("overspend_pct") is None:
        if float(row.get("plan") or 0) <= 0:
            return f"{title}: факт без плана {rub} ₽"
        return f"{title}: перерасход на {rub} ₽"
    pct = int(row.get("overspend_pct") or 0)
    return f"{title}: перерасход на {rub} ₽ (+{pct}%)"


def _build_report_lines(plan: dict[str, Any]) -> list[str]:
    """Canonical Valet first-screen report (UI brief + fallback + LLM plain)."""
    lines: list[str] = []
    date_s = plan.get("as_of_display") or plan.get("as_of") or "—"
    lines.append(str(date_s))

    plan_rub = plan.get("total_plan")
    fact_rub = plan.get("total_fact_to_date")
    spent_pct = plan.get("month_budget_spent_pct")
    exp_pct = plan.get("expected_spent_pct_by_calendar")
    if plan_rub is not None and spent_pct is not None and exp_pct is not None:
        # plan % for display = expected calendar share of month budget (day progress)
        lines.append(f"Расходы план {_rub(plan_rub)}, {exp_pct}%")
        lines.append(f"Расходы факт {_rub(fact_rub)}, {spent_pct}%")
    elif plan_rub is not None:
        lines.append(f"Расходы план {_rub(plan_rub)}")
        lines.append(f"Расходы факт {_rub(fact_rub)}")

    d_rub = plan.get("delta_vs_expected_rub")
    d_pct = plan.get("delta_vs_expected_pct")
    if d_rub is not None and d_pct is not None:
        if d_rub > 0:
            lines.append(f"На {_rub(d_rub)} и {abs(d_pct)}% больше плана")
        elif d_rub < 0:
            lines.append(f"На {_rub(abs(d_rub))} и {abs(d_pct)}% меньше плана")
        else:
            lines.append("Ровно по календарному темпу плана")

    alerts = [a for a in (plan.get("overspend_alerts") or []) if isinstance(a, dict)]
    watched = [w for w in (plan.get("watched") or []) if isinstance(w, dict)]
    if alerts or watched:
        lines.append("")
        lines.append("Обратить внимание:")
        n = 1
        for a in alerts[:6]:
            lines.append(f"{n}. {_fmt_alert_line(a)}")
            n += 1
        for w in watched[:8]:
            # skip if already listed as alert with same key
            key = f"{w.get('category')}::{w.get('expense') or ''}"
            if any(f"{a.get('category')}::{a.get('expense') or ''}" == key for a in alerts[:6]):
                continue
            lines.append(f"{n}. {_fmt_watch_line(w)} (!)")
            n += 1

    lines.append("")
    lines.append(f"Касса: {_rub(plan.get('kassa'))} из них")
    for acc in plan.get("cash_accounts") or []:
        if not isinstance(acc, dict):
            continue
        lines.append(f"{acc.get('name') or 'Счёт'} — {_rub(acc.get('amount'))}")
    lines.append("")
    lines.append(f"Отложено {_rub(plan.get('reserved'))}")
    lines.append(f"Свободных денег {_rub(plan.get('free_money'))}")
    lines.append("")
    rem = plan.get("remaining_planned_expenses")
    lines.append(f"Остаток запланированных расходов до конца месяца {_rub(rem)}")
    after = plan.get("free_after_plan")
    if after is not None:
        if after >= 0:
            lines.append(f"С учётом свободных денег останется {_rub(after)}")
        else:
            lines.append(f"С учётом свободных денег не хватает {_rub(abs(after))}")
    drawer_n = plan.get("drawer_count")
    if drawer_n is None:
        gaps = plan.get("gaps") if isinstance(plan.get("gaps"), dict) else {}
        drawer_n = gaps.get("drawer_ops")
    try:
        drawer_n = int(drawer_n or 0)
    except (TypeError, ValueError):
        drawer_n = 0
    lines.append("")
    lines.append(f"В ящике: {drawer_n}")
    return lines


def _plan_plain(plan: dict[str, Any]) -> str:
    return "\n".join(_build_report_lines(plan))


def _sanitize_reply(text: str, plan: dict[str, Any]) -> str:
    t = (text or "").strip()
    if not t:
        return _fallback_briefing(plan)
    t = re.sub(r"```[\w-]*\s*[\s\S]*?```", "", t).strip()
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        return _fallback_briefing(plan)
    if re.search(r'"(total_queue_count|hot_categories_impact|balance_status|next_step)"\s*:', t):
        t = re.sub(r"\{[^{}]*\}", "", t).strip()
        if not t or len(t) < 40:
            return _fallback_briefing(plan)
    return t


def provider() -> str:
    return (os.getenv("VALET_LLM_PROVIDER") or "timeweb").strip().lower()


def _openrouter_key() -> str:
    return (os.getenv("OPENROUTER_API_KEY") or "").strip()


def _model() -> str:
    return (os.getenv("VALET_LLM_MODEL") or "google/gemini-2.5-flash").strip()


def _timeweb_key() -> str:
    return (os.getenv("TIMEWEB_AI_API_KEY") or "").strip()


def _timeweb_agent_id() -> str:
    return (os.getenv("TIMEWEB_AI_AGENT_ID") or "").strip()


def _timeweb_base() -> str:
    return (os.getenv("TIMEWEB_AI_BASE_URL") or "https://agent.timeweb.cloud").rstrip("/")


def _compose_user_blob(
    messages: list[dict[str, str]],
    plan: dict[str, Any],
    knowledge: str = "",
) -> str:
    """Timeweb /call accepts a single message — pack context + dialogue."""
    hist = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            hist.append(f"{role.upper()}: {content}")
    hist_txt = "\n".join(hist) if hist else "USER: (ежедневный доклад при встрече)"
    return (
        SYSTEM_PHASE0
        + "\n\n=== сводка плана (единственный источник цифр) ===\n"
        + _plan_plain(plan)
        + "\n\n=== шпаргалка продукта (единственный источник про UI/возможности) ===\n"
        + (knowledge or "(пусто)")
        + "\n\n=== диалог ===\n"
        + hist_txt
        + "\n\nОтветь как Валет обычным текстом для Макса. Без JSON и без кодблоков."
        + " Если не знаешь — начни с [[DONT_KNOW]]."
    )


def _fallback_briefing(plan: dict[str, Any]) -> str:
    name = plan.get("user_display_name") or "Макс"
    return f"Привет, {name}.\n" + "\n".join(_build_report_lines(plan))


async def chat_timeweb(
    *,
    messages: list[dict[str, str]],
    plan: dict[str, Any],
    knowledge: str = "",
) -> dict[str, Any]:
    key = _timeweb_key()
    agent_id = _timeweb_agent_id()
    if not key or not agent_id:
        return {
            "ok": False,
            "error": "TIMEWEB_AI_API_KEY or TIMEWEB_AI_AGENT_ID missing",
            "reply": _fallback_briefing(plan),
            "source": "fallback",
        }
    url = f"{_timeweb_base()}/api/v1/cloud-ai/agents/{agent_id}/call"
    body = {"message": _compose_user_blob(messages, plan, knowledge), "parent_message_id": ""}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            res = await client.post(url, headers=headers, json=body)
        if res.status_code >= 400:
            return {
                "ok": False,
                "error": f"timeweb {res.status_code}: {res.text[:400]}",
                "reply": _fallback_briefing(plan),
                "source": "fallback",
            }
        data = res.json()
        reply = _sanitize_reply(
            (data.get("message") or data.get("content") or "").strip(), plan
        )
        return {
            "ok": True,
            "reply": reply,
            "source": "timeweb",
            "model": "timeweb-agent",
            "usage": None,
            "message_id": data.get("id"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "reply": _fallback_briefing(plan),
            "source": "fallback",
        }


async def chat_openrouter(
    *,
    messages: list[dict[str, str]],
    plan: dict[str, Any],
    knowledge: str = "",
) -> dict[str, Any]:
    key = _openrouter_key()
    if not key:
        return {
            "ok": False,
            "error": "OPENROUTER_API_KEY missing",
            "reply": _fallback_briefing(plan),
            "source": "fallback",
        }

    sys = (
        SYSTEM_PHASE0
        + "\n\nСводка плана:\n"
        + _plan_plain(plan)
        + "\n\nШпаргалка продукта:\n"
        + (knowledge or "(пусто)")
    )
    body = {
        "model": _model(),
        "messages": [{"role": "system", "content": sys}, *messages],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://wallet.islanddream.ru",
        "X-Title": "Wallet Valet",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=body,
            )
        if res.status_code >= 400:
            return {
                "ok": False,
                "error": f"openrouter {res.status_code}: {res.text[:400]}",
                "reply": _fallback_briefing(plan),
                "source": "fallback",
            }
        data = res.json()
        reply = _sanitize_reply(
            (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip(),
            plan,
        )
        return {
            "ok": True,
            "reply": reply,
            "source": "openrouter",
            "model": data.get("model") or _model(),
            "usage": data.get("usage") or {},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "reply": _fallback_briefing(plan),
            "source": "fallback",
        }


async def valet_reply(
    *,
    user_messages: list[dict[str, str]],
    plan: dict[str, Any],
    greeting: bool = False,
    knowledge: str = "",
    probe_openrouter: bool = False,
) -> dict[str, Any]:
    from critical_alerts import (
        openrouter_alert_from_error,
        probe_openrouter as _probe_or,
        remember_openrouter_failure,
        remember_openrouter_ok,
    )

    if greeting and not user_messages:
        user_messages = [
            {
                "role": "user",
                "content": (
                    "Сформируй короткий доклад по сводке: приветствие и дальше теми же блоками, "
                    "что в сводке (дата, план/факт, внимание, касса, свободные, остаток). "
                    "Не добавляй лишних разделов. Без JSON. Не используй [[DONT_KNOW]]. "
                    "Блок «Каналы» в конце НЕ пиши — его добавит система отдельно."
                ),
            }
        ]

    critical: list[dict[str, Any]] = []
    # On greeting (and when asked) surface known OpenRouter outages without blocking chat.
    if probe_openrouter or greeting:
        alert = await _probe_or(force=False)
        if alert:
            critical.append(alert)

    prov = provider()
    if prov == "openrouter":
        result = await chat_openrouter(messages=user_messages, plan=plan, knowledge=knowledge)
        if result.get("ok"):
            remember_openrouter_ok()
            result["critical_alerts"] = critical
            return result
        err = result.get("error") or "OpenRouter failed"
        remember_openrouter_failure(err)
        critical.append(
            openrouter_alert_from_error(
                err,
                context="Чат Валета",
                fallback="Timeweb",
            )
        )
        tw = await chat_timeweb(messages=user_messages, plan=plan, knowledge=knowledge)
        if tw.get("ok"):
            tw["error"] = f"{err} → fallback timeweb"
            tw["critical_alerts"] = critical
            return tw
        result["critical_alerts"] = critical
        return result

    # default: timeweb
    result = await chat_timeweb(messages=user_messages, plan=plan, knowledge=knowledge)
    result["critical_alerts"] = critical
    return result


async def valet_draft_deferred_answer(
    *,
    question: str,
    knowledge: str,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draft a deferred answer from product FAQ (+ optional plan context)."""
    plan = plan or {}
    messages = [
        {
            "role": "user",
            "content": (
                "Пользователь ранее задал вопрос, на который у тебя не было ответа. "
                "Составь короткий честный ответ ТОЛЬКО по шпаргалке продукта и сводке плана. "
                "Не выдумывай функции. Если в шпаргалке ответа всё ещё нет — напиши [[DONT_KNOW]].\n\n"
                f"Вопрос пользователя:\n{question}"
            ),
        }
    ]
    return await valet_reply(user_messages=messages, plan=plan, greeting=False, knowledge=knowledge)
