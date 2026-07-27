"""Critical system alerts for Valet chat (OpenRouter outages, forced fallbacks, etc.)."""

from __future__ import annotations

import time
from typing import Any

import httpx

# Cache OpenRouter probe so we don't spam on every chat turn.
_OR_PROBE: dict[str, Any] = {"checked_at": 0.0, "ok": None, "error": None, "http_status": None}
_OR_PROBE_TTL_SEC = 15 * 60  # 15 minutes


def make_alert(
    *,
    code: str,
    title: str,
    detail: str,
    provider: str = "",
    fallback: str = "",
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "severity": "critical",
        "code": code,
        "title": title,
        "detail": (detail or "").strip()[:500],
        "provider": provider,
        "fallback": fallback,
        "http_status": http_status,
    }


def format_alert_message(alert: dict[str, Any]) -> str:
    ue = alert.get("user_error")
    if isinstance(ue, dict):
        title = alert.get("title") or ue.get("user_message") or "Ошибка обработки"
        lines = [f"⚠️ {title}"]
        msg = (ue.get("user_message") or alert.get("detail") or "").strip()
        if msg and msg != title:
            lines.append(msg[:500])
        src = str(ue.get("source") or "").strip()
        stage = str(ue.get("stage") or "").strip()
        et = str(ue.get("error_type") or "").strip()
        tag = "/".join(p for p in (src, stage, et) if p)
        if tag:
            lines.append(f"[{tag}]")
        tech = (ue.get("technical_message") or "").strip()
        if tech:
            lines.append(f"Технически: {tech[:300]}")
        return "\n".join(lines)

    title = alert.get("title") or "Критическая ошибка"
    lines = [f"⚠️ {title}"]
    detail = (alert.get("detail") or "").strip()
    if detail:
        lines.append(detail)
    fb = (alert.get("fallback") or "").strip()
    if fb:
        lines.append(f"Запасной канал: {fb}. Процесс не прерван.")
    else:
        lines.append("Нужно вмешательство — без починки часть функций может деградировать.")
    return "\n".join(lines)


def format_alerts_message(alerts: list[dict[str, Any]]) -> str:
    parts = [format_alert_message(a) for a in alerts if isinstance(a, dict)]
    return "\n\n".join(parts)


def openrouter_alert_from_error(
    error: str,
    *,
    context: str,
    fallback: str = "",
    http_status: int | None = None,
) -> dict[str, Any]:
    return make_alert(
        code="openrouter_unavailable",
        title="OpenRouter недоступен",
        detail=f"{context}: {error}" if context else error,
        provider="openrouter",
        fallback=fallback,
        http_status=http_status,
    )


async def probe_openrouter(*, force: bool = False) -> dict[str, Any] | None:
    """Lightweight OpenRouter health check. Returns a critical alert if down, else None."""
    import os

    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return make_alert(
            code="openrouter_missing_key",
            title="OpenRouter не настроен",
            detail="OPENROUTER_API_KEY отсутствует в окружении API.",
            provider="openrouter",
            fallback="Timeweb / локальный разбор",
        )

    now = time.time()
    if (
        not force
        and _OR_PROBE.get("ok") is not None
        and (now - float(_OR_PROBE.get("checked_at") or 0)) < _OR_PROBE_TTL_SEC
    ):
        if _OR_PROBE.get("ok"):
            return None
        return openrouter_alert_from_error(
            str(_OR_PROBE.get("error") or "недоступен"),
            context="Проверка канала",
            fallback="Timeweb",
            http_status=_OR_PROBE.get("http_status"),
        )

    model = (os.getenv("VALET_LLM_MODEL") or "google/gemini-2.5-flash").strip()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://wallet.islanddream.ru",
        "X-Title": "Wallet OpenRouter Probe",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=body,
            )
        if res.status_code >= 400:
            err = f"HTTP {res.status_code}: {res.text[:200]}"
            _OR_PROBE.update(
                checked_at=now, ok=False, error=err, http_status=res.status_code
            )
            return openrouter_alert_from_error(
                err,
                context="Проверка канала",
                fallback="Timeweb",
                http_status=res.status_code,
            )
        _OR_PROBE.update(checked_at=now, ok=True, error=None, http_status=res.status_code)
        return None
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:200]
        _OR_PROBE.update(checked_at=now, ok=False, error=err, http_status=None)
        return openrouter_alert_from_error(err, context="Проверка канала", fallback="Timeweb")


def remember_openrouter_failure(error: str, http_status: int | None = None) -> None:
    """Update probe cache when a real OpenRouter call fails."""
    _OR_PROBE.update(
        checked_at=time.time(),
        ok=False,
        error=(error or "ошибка")[:300],
        http_status=http_status,
    )


def remember_openrouter_ok() -> None:
    _OR_PROBE.update(checked_at=time.time(), ok=True, error=None, http_status=200)


def build_system_status_report(
    alerts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mandatory Valet report block: channels OK or critical.

    If alerts is None — use probe cache.
    If alerts is [] — treat OpenRouter as OK (after successful probe).
    """
    if alerts is None:
        ok = _OR_PROBE.get("ok")
        if ok is True:
            alerts = []
        elif ok is False:
            alerts = [
                openrouter_alert_from_error(
                    str(_OR_PROBE.get("error") or "недоступен"),
                    context="Проверка канала",
                    fallback="Timeweb",
                    http_status=_OR_PROBE.get("http_status"),
                )
            ]
        else:
            return {
                "ok": None,
                "lines": [
                    "Каналы:",
                    "OpenRouter — статус пока не проверен",
                    "Timeweb — ок (основной чат)",
                ],
                "report_text": (
                    "Каналы:\n"
                    "OpenRouter — статус пока не проверен\n"
                    "Timeweb — ок (основной чат)"
                ),
                "alerts": [],
            }

    crit = [a for a in (alerts or []) if isinstance(a, dict)]
    lines = ["Каналы:"]
    if not crit:
        lines.append("OpenRouter — ок")
        lines.append("Timeweb — ок")
        return {
            "ok": True,
            "lines": lines,
            "report_text": "\n".join(lines),
            "alerts": [],
        }

    # Prefer naming OpenRouter explicitly when that's the failing provider
    or_alerts = [a for a in crit if (a.get("provider") or "") == "openrouter" or "openrouter" in str(a.get("code") or "")]
    other = [a for a in crit if a not in or_alerts]
    if or_alerts:
        a = or_alerts[0]
        detail = (a.get("detail") or a.get("title") or "недоступен").strip()
        lines.append(f"⚠️ OpenRouter — сбой")
        if detail:
            lines.append(detail)
        fb = (a.get("fallback") or "Timeweb").strip()
        lines.append(f"Запасной канал: {fb}. Процесс не прерван.")
    else:
        lines.append("OpenRouter — ок")
    for a in other:
        title = a.get("title") or a.get("code") or "сбой"
        lines.append(f"⚠️ {title}")
        if a.get("detail"):
            lines.append(str(a["detail"]))
    if not or_alerts:
        lines.append("Timeweb — ок")
    elif not any("Timeweb" in (x or "") for x in lines):
        lines.append("Timeweb — ок (запасной / основной чат)")

    return {
        "ok": False,
        "lines": lines,
        "report_text": "\n".join(lines),
        "alerts": crit,
    }
