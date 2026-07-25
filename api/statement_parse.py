"""Parse T-Bank statement PDFs and operation screenshots into import rows."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Any

import httpx

# Normalized row: {date, store, amount, comment, bank}
DATE_RE = re.compile(
    r"(?P<date>\d{2}\.\d{2}\.\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?)"
)
AMOUNT_RE = re.compile(
    r"(?P<amt>[−\-–+]?\s*\d{1,3}(?:[\s\u00a0\u202f]\d{3})*(?:[.,]\d{2})?|\d+[.,]\d{2})"
)


def _clean_amount(raw: str) -> float | None:
    s = (raw or "").strip()
    s = s.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    s = s.replace("−", "-").replace("–", "-")
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = parts[0].replace(".", "") + "." + parts[1]
        else:
            s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    if neg:
        v = -abs(v)
    return v


def _date_to_iso(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("T", " ")
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?))?", s)
    if m:
        d, mo, y, t = m.groups()
        return f"{y}-{mo}-{d}" + (f"T{t}" if t else "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}:\d{2}(?::\d{2})?))?", s)
    if m:
        y, mo, d, t = m.groups()
        return f"{y}-{mo}-{d}" + (f"T{t}" if t else "")
    return s


def normalize_row(
    *,
    date: str,
    store: str,
    amount: float,
    comment: str = "",
    bank: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    store = (store or "").strip()
    if not store:
        return None
    amt = float(amount)
    if amt == 0:
        return None
    # expense < 0; positive amounts keep as unclear (income / refund / hold)
    kind = "expense" if amt < 0 else "unclear"
    date_iso = _date_to_iso(date) if date else ""
    if not date_iso:
        return None
    b = bank or {}
    return {
        "date": date_iso,
        "store": store,
        "amount": amt,
        "kind": kind,
        "comment": (comment or "").strip(),
        "bank": {
            "desc": b.get("desc") or store,
            "category": b.get("category") or "",
            "card": b.get("card") or "",
            "mcc": b.get("mcc") or "",
            "payDate": b.get("payDate") or "",
            "opDateRaw": b.get("opDateRaw") or date,
            "status": b.get("status") or "OK",
        },
    }


def _rows_from_table(headers: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not headers or not rows:
        return []
    h = [str(x or "").strip().lower() for x in headers]
    def col(*names: str) -> int:
        for n in names:
            if n in h:
                return h.index(n)
        for i, hh in enumerate(h):
            if any(n in hh for n in names):
                return i
        return -1

    i_date = col("дата операции", "дата", "date")
    i_desc = col("описание", "операция", "merchant", "магазин")
    i_amt = col("сумма операции", "сумма", "amount")
    i_cat = col("категория", "category")
    i_card = col("номер карты", "карта", "card")
    i_mcc = col("mcc")
    i_status = col("статус", "status")
    if i_date < 0 or i_desc < 0 or i_amt < 0:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        cols = [str(c or "").strip() for c in row]
        if len(cols) < max(i_date, i_desc, i_amt) + 1:
            continue
        status = cols[i_status].upper() if i_status >= 0 and i_status < len(cols) else "OK"
        if status and status not in ("OK", ""):
            continue
        amt = _clean_amount(cols[i_amt])
        if amt is None or amt == 0:
            continue
        cat = cols[i_cat] if 0 <= i_cat < len(cols) else ""
        card = cols[i_card] if 0 <= i_card < len(cols) else ""
        mcc = cols[i_mcc] if 0 <= i_mcc < len(cols) else ""
        comment = " · ".join(x for x in [cat, f"карта {card}" if card else "", f"MCC {mcc}" if mcc else ""] if x)
        nr = normalize_row(
            date=cols[i_date],
            store=cols[i_desc],
            amount=amt,
            comment=comment,
            bank={"desc": cols[i_desc], "category": cat, "card": card, "mcc": mcc, "opDateRaw": cols[i_date]},
        )
        if nr:
            out.append(nr)
    return out


def _rows_from_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        s = re.sub(r"\s+", " ", (line or "").strip())
        if len(s) < 8:
            continue
        if re.search(r"(баланс|итого|выписк|расчетный период|остаток|cashback|кэшбэк)", s, re.I):
            continue
        dm = DATE_RE.search(s)
        if not dm:
            continue
        amounts = list(AMOUNT_RE.finditer(s))
        if not amounts:
            continue
        amt_m = amounts[-1]
        amt = _clean_amount(amt_m.group("amt"))
        if amt is None or amt == 0:
            continue
        date_raw = dm.group("date")
        mid = s[dm.end() : amt_m.start()].strip(" |;\t-")
        if not mid or len(mid) < 2:
            continue
        nr = normalize_row(date=date_raw, store=mid, amount=amt, bank={"desc": mid, "opDateRaw": date_raw})
        if nr:
            out.append(nr)
    return out


def parse_pdf_bytes(data: bytes) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber not installed") from exc

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    pages = 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue
                parsed = _rows_from_table(tbl[0], tbl[1:])
                if parsed:
                    rows.extend(parsed)
            text = page.extract_text() or ""
            if text:
                rows.extend(_rows_from_text_lines(text.splitlines()))

    # dedupe within file by fingerprint-ish key
    seen = set()
    unique: list[dict[str, Any]] = []
    for r in rows:
        key = (r["date"], r["store"].lower(), r["amount"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    if not unique:
        warnings.append("Не удалось извлечь операции из PDF — попробуйте CSV или скриншот.")
    return {"rows": unique, "warnings": warnings, "meta": {"pages": pages, "engine": "pdfplumber"}}


VISION_PROMPT = """Ты извлекаешь операции из скриншота банковского приложения (Т‑Банк / Tinkoff).
Верни ТОЛЬКО JSON-массив без markdown. Каждый элемент:
{"date":"DD.MM.YYYY или YYYY-MM-DD","store":"описание/магазин/ФИО","amount":-123.45,"comment":"","category":"категория банка как на экране","card":""}
Правила:
- бери ВСЕ операции со знаком суммы как на экране: расходы amount отрицательный (−690), поступления/возвраты — положительный (+5555, +690);
- переводы людям (ФИО вроде «Наталья Ф.») и категория «Переводы» — ВКЛЮЧАЙ, category="Переводы";
- пропускай только явные переводы между своими счетами / пополнением своих карт;
- если даты нет — пропусти строку;
- category копируй с экрана (Супермаркеты, Каршеринг, Переводы, Фастфуд…);
- КРИТИЧНО: не выдумывай операции. Если на изображении нет читаемых строк — верни строго [];
- если ничего не видно — верни [].
"""


def _vision_messages(mime: str, b64: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]


def _rows_from_vision_text(text: str, *, engine: str, model: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        raw_rows = json.loads(text)
    except json.JSONDecodeError:
        return {
            "rows": [],
            "warnings": ["OCR вернул не-JSON — попробуйте другой скрин или CSV"],
            "meta": {"engine": engine, "model": model, "raw": text[:500]},
        }
    if not isinstance(raw_rows, list):
        return {"rows": [], "warnings": ["OCR JSON не массив"], "meta": {"engine": engine, "model": model}}

    out: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        amt = item.get("amount")
        if isinstance(amt, str):
            amt = _clean_amount(amt)
        if amt is None:
            continue
        cat = str(item.get("category") or "")
        card = str(item.get("card") or "")
        comment = str(item.get("comment") or "")
        if not comment and (cat or card):
            comment = " · ".join(x for x in [cat, f"карта {card}" if card else ""] if x)
        nr = normalize_row(
            date=str(item.get("date") or ""),
            store=str(item.get("store") or item.get("description") or ""),
            amount=float(amt),
            comment=comment,
            bank={
                "desc": str(item.get("store") or ""),
                "category": cat,
                "card": card,
                "opDateRaw": str(item.get("date") or ""),
            },
        )
        if nr:
            out.append(nr)
    warnings: list[str] = []
    if not out:
        warnings.append("На скриншоте не найдено расходов")
    return {"rows": out, "warnings": warnings, "meta": {"engine": engine, "model": model}}


async def _ocr_via_timeweb(mime: str, b64: str) -> dict[str, Any] | None:
    """OpenAI-compatible Timeweb agent endpoint (supports vision). Returns None if not configured."""
    key = (os.getenv("TIMEWEB_AI_API_KEY") or "").strip()
    agent_id = (os.getenv("TIMEWEB_AI_AGENT_ID") or "").strip()
    if not key or not agent_id:
        return None
    base = (os.getenv("TIMEWEB_AI_BASE_URL") or "https://agent.timeweb.cloud").rstrip("/")
    url = f"{base}/api/v1/cloud-ai/agents/{agent_id}/v1/chat/completions"
    body = {
        "model": "gpt-4o-mini",  # ignored by Timeweb; agent model is used
        "messages": _vision_messages(mime, b64),
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, headers=headers, json=body)
    if res.status_code >= 400:
        return {
            "rows": [],
            "warnings": [f"Timeweb OCR HTTP {res.status_code}: {res.text[:200]}"],
            "meta": {"engine": "timeweb"},
            "_failed": True,
        }
    payload = res.json()
    text = (payload.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    model = str(payload.get("model") or "timeweb-agent")
    return _rows_from_vision_text(text, engine="timeweb", model=model)


async def _ocr_via_openrouter(mime: str, b64: str) -> dict[str, Any] | None:
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    model = (os.getenv("VALET_LLM_MODEL") or "google/gemini-2.5-flash").strip()
    if not key:
        return None
    body = {
        "model": model,
        "messages": _vision_messages(mime, b64),
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://wallet.islanddream.ru",
        "X-Title": "Wallet Statement OCR",
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
    if res.status_code >= 400:
        return {
            "rows": [],
            "warnings": [f"OpenRouter OCR HTTP {res.status_code}: {res.text[:200]}"],
            "meta": {"engine": "openrouter"},
            "_failed": True,
        }
    payload = res.json()
    text = (payload.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    return _rows_from_vision_text(text, engine="openrouter", model=str(payload.get("model") or model))


async def parse_image_bytes(data: bytes, mime: str) -> dict[str, Any]:
    from critical_alerts import (
        openrouter_alert_from_error,
        remember_openrouter_failure,
        remember_openrouter_ok,
    )

    b64 = base64.b64encode(data).decode("ascii")
    critical: list[dict[str, Any]] = []
    soft_warnings: list[str] = []

    # Primary: OpenRouter. Fallback: Timeweb — process must not stop.
    orr = await _ocr_via_openrouter(mime, b64)
    if orr is not None and not orr.pop("_failed", False):
        remember_openrouter_ok()
        out = dict(orr)
        out["critical_alerts"] = []
        return out

    if orr is not None:
        soft_warnings.extend(orr.get("warnings") or [])
        err = (orr.get("warnings") or ["OpenRouter OCR failed"])[0]
        status = None
        if "HTTP " in err:
            try:
                status = int(err.split("HTTP ", 1)[1].split(":", 1)[0].strip())
            except ValueError:
                status = None
        remember_openrouter_failure(err, status)
        critical.append(
            openrouter_alert_from_error(
                err,
                context="OCR скриншота",
                fallback="Timeweb vision",
                http_status=status,
            )
        )

    tw = await _ocr_via_timeweb(mime, b64)
    if tw is not None and not tw.pop("_failed", False):
        out = dict(tw)
        warns = list(out.get("warnings") or [])
        # Keep non-critical empty-scan note; critical goes separately.
        out["warnings"] = warns
        out["critical_alerts"] = critical
        return out

    if tw is not None:
        soft_warnings.extend(tw.get("warnings") or [])

    if not soft_warnings and not critical:
        soft_warnings.append("OCR недоступен: нет TIMEWEB_AI_* и OPENROUTER_API_KEY")
    return {
        "rows": [],
        "warnings": soft_warnings,
        "critical_alerts": critical,
        "meta": {"engine": "none"},
    }


async def parse_statement_upload(data: bytes, filename: str, content_type: str) -> dict[str, Any]:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    if name.endswith(".pdf") or ctype == "application/pdf":
        parsed = parse_pdf_bytes(data)
        parsed["source"] = "pdf"
        parsed.setdefault("critical_alerts", [])
        return parsed

    if ctype.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif")):
        mime = ctype if ctype.startswith("image/") else "image/jpeg"
        if name.endswith(".png") or mime == "image/png":
            mime = "image/png"
        elif name.endswith(".webp") or mime == "image/webp":
            mime = "image/webp"
        elif name.endswith((".heic", ".heif")) or "heic" in mime or "heif" in mime:
            mime = "image/heic"
        elif name.endswith((".jpg", ".jpeg")) or mime in ("image/jpeg", "image/jpg"):
            mime = "image/jpeg"
        parsed = await parse_image_bytes(data, mime)
        parsed["source"] = "image"
        parsed.setdefault("critical_alerts", [])
        return parsed

    raise ValueError("Поддерживаются PDF и изображения (PNG/JPG/WebP). CSV загружайте через кнопку импорта как раньше.")
