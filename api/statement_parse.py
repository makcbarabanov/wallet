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
# Legacy loose amount (dates/card tails can match) — DO NOT use for PDF certificate.
AMOUNT_RE = re.compile(
    r"(?P<amt>[−\-–+]?\s*\d{1,3}(?:[\s\u00a0\u202f]\d{3})*(?:[.,]\d{2})?|\d+[.,]\d{2})"
)
# Money only: must have kopecks; optional ₽ after. Never bare card digits / year.
MONEY_RE = re.compile(
    r"(?P<amt>[−\-–+]?\s*\d{1,3}(?:[\s\u00a0\u202f]\d{3})*[.,]\d{2})\s*(?:₽|руб\.?)?"
)
CARD_TAIL_RE = re.compile(r"(?:\*?\d{4})\s*$")
OP_MAIN_RE = re.compile(
    r"^(?P<d1>\d{2}\.\d{2}\.\d{4})\s+(?P<d2>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<rest>.+)$"
)
TIME_CONT_RE = re.compile(r"^\d{1,2}:\d{2}\b")
SKIP_LINE_RE = re.compile(
    r"(баланс|итого|выписк|расчетный период|остаток|cashback|кэшбэк|"
    r"справка о движении|акционерн|лицевого счета|дата заключения|"
    r"дата и время|операции списания|универсальная лицензия|"
    r"бik\b|инн\b|к/с\b)",
    re.I,
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


SIGN_PREFIX = "−–-+"


def _strip_amount_signs(raw: str) -> str:
    s = (raw or "").strip()
    while s and s[0] in SIGN_PREFIX:
        s = s[1:]
    return s


def _is_plausible_money(amt: float, raw: str, line: str, span: tuple[int, int]) -> bool:
    """Reject dates, card tails, years — only real money with kopecks."""
    if amt is None or amt == 0:
        return False
    abs_amt = abs(amt)
    if abs_amt >= 100000000:  # absurd
        return False
    raw_n = re.sub(r"[\s\u00a0\u202f]", "", _strip_amount_signs(raw or ""))
    # Must look like money with decimals (already required by MONEY_RE).
    if not re.search(r"[.,]\d{2}$", raw_n):
        return False
    start, end = span
    window = line[max(0, start - 2) : min(len(line), end + 4)]
    # Date fragment DD.MM.YYYY — money sits inside a date
    if re.search(r"\d{2}\.\d{2}\.\d{4}", line[max(0, start - 6) : end + 6]):
        # Allow if this match is clearly a money token with ₽ or sign+spaces pattern
        has_rub = "₽" in window or bool(re.search(r"руб", window, re.I))
        has_sign = bool(re.match(r"[−\-–+]", (raw or "").strip()))
        if not (has_rub or has_sign):
            # e.g. matching inside 26.07.2026
            if re.fullmatch(r"\d{2}\.\d{2}", raw_n):
                return False
    # Card last-4 alone (0507) never has decimals — already filtered.
    # Trailing card digits after money are fine (money span ends before them).
    return True


def _extract_money_tokens(line: str) -> list[tuple[float, str, tuple[int, int]]]:
    found: list[tuple[float, str, tuple[int, int]]] = []
    for m in MONEY_RE.finditer(line):
        raw = m.group("amt")
        amt = _clean_amount(raw)
        if amt is None:
            continue
        # Prefer explicit + for income
        if "+" in raw or (raw.strip().startswith("+")):
            amt = abs(amt)
        elif re.match(r"[−\-–]", raw.strip()) or "-" in raw[:2]:
            amt = -abs(amt)
        if not _is_plausible_money(amt, raw, line, m.span()):
            continue
        # Drop tokens that are clearly the DD.MM part of a date (no rub, value looks like day.month)
        if re.fullmatch(r"\d{1,2}[.,]\d{2}", re.sub(r"\s", "", _strip_amount_signs(raw))):
            # 26.07 as money would be 26.07 rub — rare; skip if surrounded by date context
            around = line[max(0, m.start() - 1) : m.end() + 5]
            if re.match(r".*\d{2}[.,]\d{2}\.\d{4}", around) or re.search(r"\.\d{4}", around):
                continue
        found.append((amt, raw, m.span()))
    return found


def _looks_like_tbank_certificate(text: str) -> bool:
    t = text or ""
    if re.search(r"справка\s+о\s+движении\s+средств", t, re.I):
        return True
    # Heuristic: many lines with two dates + ₽ and no extractable tables upstream
    money_lines = sum(1 for line in t.splitlines() if MONEY_RE.search(line) and "₽" in line)
    return money_lines >= 5 and bool(re.search(r"\d{2}\.\d{2}\.\d{4}\s+\d{2}\.\d{2}\.\d{4}", t))


def _strip_card_tail(text: str) -> tuple[str, str]:
    s = (text or "").strip()
    m = CARD_TAIL_RE.search(s)
    if not m:
        return s, ""
    card = m.group(0).strip()
    # Don't strip if the whole merchant is just the card
    head = s[: m.start()].strip()
    if not head:
        return s, ""
    return head, card.lstrip("*")


def _clean_merchant(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    s = re.sub(r"^(оплата\s+в|перевод|пополнение\.?)\s*", "", s, flags=re.I)
    s = re.sub(r"\s+(SANKT-?PETERBU|PETERBU|RUS|Moskva|g\.\s*Moskva)\s*$", "", s, flags=re.I)
    s = s.strip(" ·;-")
    if re.fullmatch(r"оплата\s+в", s, flags=re.I):
        return ""
    return s


def _merchant_from_block(main_desc: str, cont_lines: list[str]) -> tuple[str, str]:
    """Return (merchant, full_description). Prefer continuation when main is card-only."""
    desc_parts = [main_desc] + [c for c in cont_lines if c]
    full = " ".join(desc_parts).strip()
    head, card = _strip_card_tail(main_desc)
    merchant = _clean_merchant(head)
    # Main line was "Оплата в 0507" → merchant empty after clean; take YANDEX*… from cont
    if len(merchant) < 2 or re.fullmatch(r"\d{4}", merchant):
        for c in cont_lines:
            c2 = re.sub(r"^\d{1,2}:\d{2}(?:\s+\d{1,2}:\d{2})?\s*", "", c).strip()
            c2 = re.sub(r"^карту\s+", "", c2, flags=re.I)
            if not c2 or len(c2) < 2:
                continue
            if re.fullmatch(r"(SANKT-?PETERBU|PETERBU|RUS|Moskva|g\.\s*Moskva)", c2, flags=re.I):
                continue
            if re.search(r"(SANKT|PETERBU|^RUS$|^g\.)", c2, re.I) and "*" not in c2 and len(c2) < 28:
                # city-only continuation
                if not re.search(r"[A-Za-z]{3,}\*", c2):
                    continue
            if "*" in c2:
                merchant = c2.split()[0]
            else:
                merchant = _clean_merchant(c2)
            if len(merchant) >= 2:
                break
    if len(merchant) < 2:
        merchant = _clean_merchant(full) or "Неизвестно"
    # Light aliases so PDF ≈ CSV store names for common chains
    aliases = {
        "PYATEROCHKA": "Пятёрочка",
        "LENTA": "Лента",
        "MAGNIT": "Магнит",
        "METRO": "Метро Санкт-Петербург",
        "DELIMOBIL": "Делимобиль",
        "YANDEX": "Yandex Cloud",
    }
    if not re.search(r"(перевод|пополнение|зачисление|внесение)", main_desc, re.I):
        up = merchant.upper()
        if "SCOOTER" in up or "7999" in up:
            merchant = "Самокаты - Яндекс Go"
        elif "OBLAKO" in up or re.match(r"YANDEX\*7372", up):
            merchant = "Yandex Cloud"
        elif up.startswith("YANDEX"):
            merchant = "Yandex Cloud"
        else:
            key = up.split("*")[0].split()[0]
            if key in aliases:
                merchant = aliases[key]
    return merchant, full


def _parse_certificate_main_line(line: str) -> dict[str, Any] | None:
    s = re.sub(r"\s+", " ", (line or "").strip())
    if len(s) < 12 or SKIP_LINE_RE.search(s):
        return None
    m = OP_MAIN_RE.match(s)
    if not m:
        return None
    money = _extract_money_tokens(s)
    # Prefer tokens that have ₽ nearby in the original line
    rub_money = []
    for amt, raw, span in money:
        after = s[span[1] : span[1] + 3]
        before_ok = True
        if "₽" in after or "₽" in s[span[0] : span[1] + 3]:
            rub_money.append((amt, raw, span))
    use = rub_money if rub_money else money
    if len(use) < 1:
        return None
    # Two amounts (currency + operation) → take the last money-with-₽ (operation column)
    amt, raw, span = use[-1]
    # Description after last money token
    desc = s[span[1] :].strip()
    desc = re.sub(r"^₽\s*", "", desc).strip()
    card = ""
    desc, card = _strip_card_tail(desc)
    if not card:
        # Card glued without space before 0507 after merchant
        cm = re.search(r"(\d{4})$", desc)
        if cm and not re.search(r"[.,]\d{2}$", desc):
            # only if looks like card (4 digits) and desc has letters before
            head = desc[: cm.start()].strip()
            if head and re.search(r"[A-Za-zА-Яа-я]", head):
                card = cm.group(1)
                desc = head
    date_raw = m.group("d1")
    # Time may appear only on continuation — keep date for now
    return {
        "date": date_raw,
        "amount": amt,
        "desc": desc,
        "card": card,
        "raw": s,
    }


def _is_continuation_line(line: str) -> bool:
    s = (line or "").strip()
    if not s or len(s) < 2:
        return False
    if SKIP_LINE_RE.search(s):
        return False
    if OP_MAIN_RE.match(re.sub(r"\s+", " ", s)) and _extract_money_tokens(s):
        return False
    if re.fullmatch(r"\d{1,3}", s):  # page number
        return False
    return True


def _rows_from_tbank_certificate(lines: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse T-Bank «Справка о движении средств» multi-line blocks → normalize_row()."""
    cleaned = [re.sub(r"\s+", " ", (ln or "").strip()) for ln in lines]
    cleaned = [ln for ln in cleaned if ln]

    blocks: list[dict[str, Any]] = []
    i = 0
    skipped_no_money = 0
    while i < len(cleaned):
        main = _parse_certificate_main_line(cleaned[i])
        if not main:
            # Dated line without valid money → do not invent an expense
            if DATE_RE.search(cleaned[i]) and "₽" in cleaned[i]:
                skipped_no_money += 1
            i += 1
            continue
        cont: list[str] = []
        j = i + 1
        while j < len(cleaned):
            nxt = cleaned[j]
            if _parse_certificate_main_line(nxt):
                break
            if not _is_continuation_line(nxt):
                break
            # Attach time from first continuation if present
            tm = TIME_CONT_RE.match(nxt)
            if tm and "T" not in main["date"]:
                main["date"] = f"{main['date']} {tm.group(0)[:5]}"
            cont.append(nxt)
            j += 1
        merchant, full_desc = _merchant_from_block(main["desc"], cont)
        card = main.get("card") or ""
        if not card:
            _, card2 = _strip_card_tail(full_desc)
            card = card2
        comment_bits = []
        if card:
            comment_bits.append(f"карта *{card[-4:]}" if not card.startswith("*") else f"карта {card}")
        comment = " · ".join(comment_bits)
        nr = normalize_row(
            date=main["date"],
            store=merchant,
            amount=float(main["amount"]),
            comment=comment,
            bank={
                "desc": full_desc or merchant,
                "category": "",
                "card": f"*{card[-4:]}" if card else "",
                "mcc": "",
                "opDateRaw": main["date"],
                "status": "OK",
            },
        )
        if nr:
            blocks.append(nr)
        i = max(j, i + 1)

    diag = {
        "format": "tbank_certificate",
        "blocks_ok": len(blocks),
        "skipped_no_money": skipped_no_money,
        "amount_ok": sum(1 for r in blocks if abs(float(r["amount"])) >= 0.01),
        "date_ok": sum(1 for r in blocks if r.get("date")),
        "bad_amount_7": sum(1 for r in blocks if abs(abs(float(r["amount"])) - 7.0) < 1e-9),
    }
    return blocks, diag


def _rows_from_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Generic one-line PDF text ops. Uses MONEY_RE only — never last bare integer."""
    out: list[dict[str, Any]] = []
    for line in lines:
        s = re.sub(r"\s+", " ", (line or "").strip())
        if len(s) < 8 or SKIP_LINE_RE.search(s):
            continue
        dm = DATE_RE.search(s)
        if not dm:
            continue
        money = _extract_money_tokens(s)
        if not money:
            continue
        # Prefer ₽-adjacent
        rub = [t for t in money if "₽" in s[t[2][0] : t[2][1] + 3]]
        amt, _raw, span = (rub[-1] if rub else money[-1])
        date_raw = dm.group("date")
        # Merchant: after money, else between date and money
        after = s[span[1] :].strip()
        after = re.sub(r"^₽\s*", "", after).strip()
        store, card = _strip_card_tail(after)
        store = _clean_merchant(store)
        if len(store) < 2:
            mid = s[dm.end() : span[0]].strip(" |;\t-")
            store = _clean_merchant(mid)
        if len(store) < 2:
            continue  # refuse to invent
        comment = f"карта *{card[-4:]}" if card else ""
        nr = normalize_row(
            date=date_raw,
            store=store,
            amount=amt,
            comment=comment,
            bank={"desc": store, "card": f"*{card[-4:]}" if card else "", "opDateRaw": date_raw},
        )
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
    all_text_parts: list[str] = []
    table_rows = 0
    diag: dict[str, Any] = {}

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
                    table_rows += len(parsed)
            text = page.extract_text() or ""
            if text:
                all_text_parts.append(text)

    full_text = "\n".join(all_text_parts)
    if _looks_like_tbank_certificate(full_text):
        # Certificate text layout: do not mix with broken per-line last-number parser.
        cert_rows, diag = _rows_from_tbank_certificate(full_text.splitlines())
        if table_rows:
            # Prefer richer table rows if any; else certificate
            pass
        rows = cert_rows if cert_rows else rows
        diag["pages"] = pages
        diag["table_rows"] = table_rows
        if not cert_rows:
            warnings.append(
                "Не удалось уверенно распознать операции в PDF-справке. "
                "Загрузите CSV выписки из банка или другой файл."
            )
        elif diag.get("bad_amount_7", 0) > max(3, len(cert_rows) // 10):
            warnings.append(
                "Подозрительные суммы в PDF (похожи на номер карты). "
                "Проверьте результат или загрузите CSV."
            )
    else:
        if full_text:
            rows.extend(_rows_from_text_lines(full_text.splitlines()))
        diag = {
            "format": "generic_pdf",
            "pages": pages,
            "table_rows": table_rows,
            "blocks_ok": len(rows),
        }

    # dedupe within file by fingerprint-ish key
    seen = set()
    unique: list[dict[str, Any]] = []
    for r in rows:
        key = (r["date"][:10], r["store"].lower(), round(float(r["amount"]), 2))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    if not unique:
        warnings.append(
            "Не удалось уверенно распознать операции из PDF — попробуйте CSV выписку банка."
        )
    meta = {
        "pages": pages,
        "engine": "pdfplumber",
        "diagnostics": diag,
        "rows_out": len(unique),
        "amount_ok": sum(1 for r in unique if abs(float(r["amount"])) >= 0.01),
        "date_ok": sum(1 for r in unique if r.get("date")),
    }
    return {"rows": unique, "warnings": warnings, "meta": meta}


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
    """OCR bank screenshot. Prefer Timeweb when VALET_LLM_PROVIDER=timeweb."""
    from critical_alerts import (
        openrouter_alert_from_error,
        remember_openrouter_failure,
        remember_openrouter_ok,
    )

    b64 = base64.b64encode(data).decode("ascii")
    critical: list[dict[str, Any]] = []
    soft_warnings: list[str] = []
    provider = (os.getenv("VALET_LLM_PROVIDER") or "timeweb").strip().lower()
    prefer_timeweb = provider in ("", "timeweb", "tw")

    def _human(raw: str, name: str) -> str:
        text = (raw or "").strip()
        low = text.lower()
        if "403" in text or "access denied" in low or "security policy" in low:
            return f"{name} отклоняет распознавание (доступ ограничен). Пробуем другой канал."
        if "HTTP " in text:
            return f"{name}: временный сбой. Пробуем другой канал."
        return text or f"{name}: сбой OCR."

    async def _try_tw() -> dict[str, Any] | None:
        tw = await _ocr_via_timeweb(mime, b64)
        if tw is None:
            return None
        if tw.pop("_failed", False):
            soft_warnings.append(_human((tw.get("warnings") or ["Timeweb OCR failed"])[0], "Timeweb"))
            return None
        return tw

    async def _try_or() -> dict[str, Any] | None:
        orr = await _ocr_via_openrouter(mime, b64)
        if orr is None:
            return None
        if orr.pop("_failed", False):
            err = (orr.get("warnings") or ["OpenRouter OCR failed"])[0]
            soft_warnings.append(_human(err, "OpenRouter"))
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
            return None
        remember_openrouter_ok()
        return orr

    engines = [_try_tw, _try_or] if prefer_timeweb else [_try_or, _try_tw]
    for attempt in engines:
        out = await attempt()
        if out is None:
            continue
        result = dict(out)
        result["critical_alerts"] = list(result.get("critical_alerts") or []) + critical
        if result.get("rows"):
            # Success — don't surface "trying other channel" noise
            result["warnings"] = [
                w for w in (result.get("warnings") or [])
                if "Пробуем другой канал" not in w and "HTTP " not in w
            ]
        elif soft_warnings and not (result.get("warnings") or []):
            result["warnings"] = soft_warnings
        return result

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
