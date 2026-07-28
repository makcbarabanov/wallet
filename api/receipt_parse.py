"""Receipt Vision parser (AD-010 Wave 1).

Separate from statement_parse.py — bank screenshots stay on the T-Bank prompt.
This module only extracts a structured receipt; it never writes expenses.

Providers (soak order):
  Yandex (OCR + YandexGPT) → Timeweb → OpenRouter (Qwen / Llama Scout / Nova Lite)
OpenRouter models: OPENROUTER_VISION_MODELS or built-in region-safe trio.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

# httpx imported lazily in OCR callers so unit tests need no network deps

RECEIPT_VISION_PROMPT = """Ты извлекаешь данные с фото МАГАЗИННОГО / ФИСКАЛЬНОГО чека (не скриншот банка).
Верни ТОЛЬКО JSON-объект без markdown:
{
  "merchant": "название магазина как на чеке",
  "date": "DD.MM.YYYY или YYYY-MM-DD",
  "total": 2350.00,
  "currency": "RUB",
  "fiscalId": "номер чека / ФПД / fiscal id если виден, иначе пустая строка",
  "receiptNumber": "то же, если fiscalId не выделен",
  "confidence": 0.0,
  "items": [
    {
      "name": "название позиции как на чеке",
      "qty": 1,
      "price": 100.0,
      "amount": 100.0,
      "category": "категория ЭТОГО товара",
      "subcategory": "подкатегория ЭТОГО товара или пусто"
    }
  ],
  "error": null
}
Правила (AD-010):
- это чек покупки: позиции + итог; НЕ лента операций банка;
- Чек — НЕ один расход. Каждая позиция items[] — отдельный кандидат в расход;
- fiscalId / receiptNumber — если на чеке есть номер/ФПД, обязательно извлеки (Exact-дедуп);
- КАТЕГОРИЮ определяй по ТОВАРУ, НЕ по магазину. Запрещено: одна category на весь чек по названию магазина (Вимос→Материалы и т.п.);
- В одном чеке могут быть разные категории (материалы, инструмент, продукты, личное);
- category — верхний уровень из ОБЫЧНЫХ категорий кошелька. Предпочитай готовые:
  Личный: Продукты, Здоровье, Напитки, Транспорт, Жильё, Связь, Подписки, Досуг, Разовые;
  Бизнес: Стройматериалы / Материалы, Инструмент / оборудование, Авто, Бизнес · прочее.
  НЕ выдумывай узкие категории вроде «Соусы», «Майонез», «Сыры», «Хлеб» — для еды из магазина используй «Продукты».
  Майонез, молоко, хлеб, крупы, мясо, овощи → category «Продукты».
- subcategory — уточнение (Расходники, Электрика, Кофе…) или "";
- СТРОГО разделяй три числа позиции:
  · qty = фактически купленное количество (0.084 кг, 3 шт…);
  · price = цена ЗА ЕДИНИЦУ измерения (руб/кг, руб/шт);
  · amount = сумма строки (сколько списали за эту позицию).
  Пример весового: qty=0.084, price=1169.91, amount=98.27 (НЕ подставляй 1169.91 в amount).
  Пример штучного: qty=1, price=129.99, amount=129.99.
  Если видишь «1КГ» / «кг» в названии — это единица цены, не количество = 1 кг.
- если есть только price и qty — amount можно не заполнять (сервер посчитает);
- date = дата чека DD.MM.YYYY или YYYY-MM-DD; если дата плохо читается / год неясен — оставь date пустым "". НИКОГДА не угадывай год;
- total = итого к оплате; если не видно — сумма items;
- confidence от 0 до 1;
- КРИТИЧНО: не выдумывай позиции. Если это не чек / нечитаемо — верни:
  {"merchant":"","date":"","total":0,"items":[],"confidence":0,"error":"not_a_receipt"|"unreadable"|"missing_total"}
- без markdown и пояснений вне JSON.
"""

RECEIPT_TEXT_PROMPT = """Ниже текст, распознанный с фото магазинного / фискального чека (OCR).
Извлеки структуру покупки. Верни ТОЛЬКО JSON-объект без markdown в том же формате:
{
  "merchant": "...",
  "date": "DD.MM.YYYY или YYYY-MM-DD",
  "total": 0,
  "currency": "RUB",
  "confidence": 0.0,
  "items": [{"name":"...","qty":1,"price":0,"amount":0,"category":"...","subcategory":""}],
  "error": null
}
Правила те же, что для фото чека (AD-010): позиции отдельно, категория по товару, не выдумывай строки.
Для еды/бакалеи (майонез, молоко, хлеб…) category = «Продукты», не «Соусы» и не другие узкие выдумки.
qty / price / amount — три разных поля: кол-во, цена за ед., сумма строки (см. пример с весовым товаром).
Если текст не похож на чек — error: "not_a_receipt" или "unreadable".

Текст чека:
"""


def _money_tol(a: float, b: float) -> float:
    """Shop rounding tolerance for price × qty ≈ amount."""
    scale = max(abs(a), abs(b), 1.0)
    return max(0.05, 0.02 * scale)


def _approx_eq(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= _money_tol(float(a), float(b))


def _infer_unit(name: str, qty: float, raw_unit: str = "") -> str:
    u = (raw_unit or "").strip().lower().replace(".", "")
    blob = f"{u} {name or ''}".lower().replace(" ", "")
    if u in ("кг", "kg", "г", "g", "гр"):
        return "кг"
    if u in ("л", "l"):
        return "л"
    if u in ("м", "m"):
        return "м"
    if u in ("шт", "pcs"):
        return "шт"
    # "1КГ", "руб/кг", plain "кг" in product name
    if "кг" in blob or "kg" in blob or "килограм" in blob:
        return "кг"
    if re.search(r"(^|[^а-яa-z])л(итр)?([^а-яa-z]|$)|/\s*л", f"{u} {name or ''}".lower()):
        return "л"
    if re.search(r"\b(шт|штук|pcs)\b", f"{u} {name or ''}".lower()):
        return "шт"
    if qty is not None and 0 < float(qty) < 0.999:
        return "кг"
    return "шт"


def normalize_receipt_line_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one receipt line: unit / qty / price / amount + math status.

    Invariant: NEVER silently fix a broken price×qty≈amount relation.
    Any mismatch → amountStatus=doubt and candidates for the user in Razbor.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("title") or "").strip()
    if not name:
        return None

    qty_raw = _num(raw.get("qty") if raw.get("qty") is not None else raw.get("quantity"))
    price_raw = _num(raw.get("price") if raw.get("price") is not None else raw.get("unitPrice"))
    amount_raw = _num(
        raw.get("amount") if raw.get("amount") is not None else raw.get("sum")
    )
    unit_hint = str(raw.get("unit") or raw.get("uom") or "").strip()

    qty = float(qty_raw) if qty_raw is not None and qty_raw > 0 else 1.0
    price = float(price_raw) if price_raw is not None else None
    amount = float(abs(amount_raw)) if amount_raw is not None else None

    unit = _infer_unit(name, qty, unit_hint)
    raw_cat = str(raw.get("category") or "").strip()
    subcategory = str(raw.get("subcategory") or raw.get("expense") or "").strip()
    category = _normalize_item_category(raw_cat, name)

    product = round(price * qty, 4) if price is not None else None
    qty_is_one = abs(qty - 1.0) <= 0.001

    status = "ok"
    doubt: dict[str, Any] | None = None

    # Case A: only amount (or amount + qty=1 without price)
    if amount is not None and price is None:
        price = round(amount / qty, 4) if qty else amount
        status = "ok" if qty_is_one else "inferred"
        product = round(price * qty, 4)

    # Case B: price + qty, amount missing → amount from formula (math holds by construction)
    elif amount is None and price is not None:
        amount = round(price * qty, 2)
        product = round(price * qty, 4)
        status = "inferred"

    # Case C: all three present — validate, never auto-fix
    elif amount is not None and price is not None:
        product = round(price * qty, 4)
        if _approx_eq(product, amount):
            status = "ok"
        elif qty_is_one and _approx_eq(price, amount):
            status = "ok"
            unit = unit if unit != "кг" else "шт"
        elif (not qty_is_one) and _approx_eq(amount, price) and product is not None and not _approx_eq(
            product, amount
        ):
            # Classic weight bug: unit price copied into amount
            status = "doubt"
            line_total = round(product, 2)
            doubt = {
                "kind": "unit_price_as_amount",
                "explanation": (
                    f"Похоже, {amount:.2f} — цена за {unit}, а стоимость строки "
                    f"{price:.2f} × {qty} = {line_total:.2f}."
                ),
                "candidates": [
                    {
                        "id": "line_total",
                        "label": f"Да, цена за {unit} → стоимость {line_total:.2f}",
                        "amount": line_total,
                        "price": price,
                        "qty": qty,
                        "unit": unit,
                    },
                    {
                        "id": "keep_amount",
                        "label": f"Оставить стоимость {amount:.2f}",
                        "amount": amount,
                        "price": amount if qty_is_one else price,
                        "qty": 1.0 if _approx_eq(amount, price) else qty,
                        "unit": "шт" if _approx_eq(amount, price) else unit,
                    },
                    {
                        "id": "manual",
                        "label": "Нет, укажу сам",
                        "amount": None,
                        "price": price,
                        "qty": qty,
                        "unit": unit,
                    },
                ],
            }
        elif not _approx_eq(product, amount):
            status = "doubt"
            doubt = {
                "kind": "conflict",
                "explanation": (
                    f"Цена {price:.2f} × кол-во {qty} = {product:.2f}, "
                    f"но сумма строки {amount:.2f}. Как записать?"
                ),
                "candidates": [
                    {
                        "id": "use_amount",
                        "label": f"Использовать сумму строки {amount:.2f}",
                        "amount": amount,
                        "price": price,
                        "qty": qty,
                        "unit": unit,
                    },
                    {
                        "id": "use_product",
                        "label": f"Использовать {price:.2f} × {qty} = {round(product, 2):.2f}",
                        "amount": round(product, 2),
                        "price": price,
                        "qty": qty,
                        "unit": unit,
                    },
                    {
                        "id": "manual",
                        "label": "Я укажу сам",
                        "amount": None,
                        "price": price,
                        "qty": qty,
                        "unit": unit,
                    },
                ],
            }
        else:
            status = "ok"
    else:
        # no usable numbers
        return None

    if amount is None or amount == 0:
        return None

    out: dict[str, Any] = {
        "name": name,
        "qty": qty,
        "price": float(price) if price is not None else float(amount),
        "amount": float(abs(amount)),
        "unit": unit,
        "category": category,
        "subcategory": subcategory,
        "amountStatus": status,
    }
    if doubt:
        out["amountDoubt"] = doubt
        # Keep raw numbers for the question; do NOT rewrite amount to "fixed" value.
    return out


def _date_to_iso(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    m = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?:[T\s](\d{2}:\d{2}(?::\d{2})?))?", s)
    if m:
        d, mo, y, t = m.groups()
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{int(mo):02d}-{int(d):02d}" + (f"T{t}" if t else "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}:\d{2}(?::\d{2})?))?", s)
    if m:
        y, mo, d, t = m.groups()
        return f"{y}-{mo}-{d}" + (f"T{t}" if t else "")
    return s


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_item_category(category: str, name: str) -> str:
    """Map invented food niches (Соусы, Сыры…) to Продукты."""
    c = (category or "").strip()
    blob = f"{c} {name or ''}".lower()
    food = re.compile(
        r"майонез|соус|кетчуп|сыр|хлеб|молоч|йогурт|кефир|сметан|бакале|снек|чипс|"
        r"шоколад|конфет|печень|консерв|овощ|фрукт|яблок|банан|мяс|колбас|сосиск|"
        r"рыб|крупа|макарон|рис|масло|сахар|соль|мука|яйц|напиток|сок|вода|чай|кофе|"
        r"пиво|вино|алко|ряба|магнит|пятероч|лента|перекр",
        re.I,
    )
    already_ok = re.compile(
        r"^(продукты|напитки|здоровье|аптека|бытовая химия|хозяйственные|животные|дети)",
        re.I,
    )
    if food.search(blob) and not already_ok.match(c):
        return "Продукты"
    return c or "Продукты"


def normalize_receipt_payload(raw: dict[str, Any], *, engine: str, model: str) -> dict[str, Any]:
    err = raw.get("error")
    merchant = str(raw.get("merchant") or raw.get("store") or "").strip()
    date_raw = str(raw.get("date") or "").strip()
    date_iso = _date_to_iso(date_raw)
    date_status = "ok"
    if not date_iso:
        date_status = "missing"
    else:
        try:
            from datetime import date as _date

            y = int(date_iso[:4])
            if y != _date.today().year:
                date_status = "anomalous_year"
        except ValueError:
            date_status = "missing"
            date_iso = ""
    total = _num(raw.get("total") or raw.get("totalAmount"))
    fiscal_id = str(raw.get("fiscalId") or raw.get("receiptNumber") or raw.get("fiscal_id") or "").strip()
    confidence = _num(raw.get("confidence"))
    if confidence is None:
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))

    items_out: list[dict[str, Any]] = []
    raw_items = raw.get("items")
    if isinstance(raw_items, list):
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            normalized = normalize_receipt_line_item(it)
            if normalized:
                items_out.append(normalized)

    if total is None and items_out:
        total = round(sum(i["amount"] for i in items_out), 2)
    if total is not None:
        total = abs(float(total))

    warnings: list[str] = []
    critical: list[dict[str, Any]] = []

    if err or (not merchant and not items_out):
        code = str(err or "unreadable")
        human = {
            "not_a_receipt": "Это не похоже на чек магазина. Загрузите фото чека (не скрин банка).",
            "unreadable": "Не удалось прочитать чек. Попробуйте ближе / при лучшем свете.",
            "missing_total": "Не удалось найти итоговую сумму на чеке.",
        }.get(code, "Не удалось распознать чек.")
        warnings.append(human)
        critical.append(
            {
                "severity": "critical",
                "code": "receipt_parse_failed",
                "title": "Ошибка распознавания чека",
                "detail": human,
                "provider": engine,
                "fallback": None,
                "http_status": None,
                "meta": {"error": code, "model": model},
            }
        )
        return {
            "ok": False,
            "receipt": {
                "merchant": merchant,
                "date": date_iso,
                "totalAmount": total or 0,
                "currency": str(raw.get("currency") or "RUB"),
                "fiscalId": fiscal_id,
                "receiptNumber": fiscal_id,
                "items": items_out,
                "confidence": confidence,
                "error": code,
                "model": model,
                "engine": engine,
            },
            "warnings": warnings,
            "critical_alerts": critical,
            "meta": {"engine": engine, "model": model},
        }

    if not items_out:
        warnings.append("Чек прочитан, но позиции не найдены — проверьте итог вручную.")
    if confidence < 0.45:
        warnings.append("Низкая уверенность распознавания — проверьте позиции перед подтверждением.")
    if date_status == "missing":
        warnings.append("Дата чека не распознана — нужно указать вручную.")
    elif date_status == "anomalous_year":
        warnings.append(f"Дата чека выглядит необычно ({date_iso}) — подтвердите или исправьте.")

    return {
        "ok": True,
        "receipt": {
            "merchant": merchant or "Магазин",
            "date": date_iso,
            "dateRaw": date_raw,
            "dateStatus": date_status,
            "totalAmount": total or 0,
            "currency": str(raw.get("currency") or "RUB"),
            "fiscalId": fiscal_id,
            "receiptNumber": fiscal_id,
            "items": items_out,
            "confidence": confidence,
            "error": None,
            "model": model,
            "engine": engine,
        },
        "warnings": warnings,
        "critical_alerts": [],
        "meta": {"engine": engine, "model": model},
    }


def _parse_vision_json(text: str, *, engine: str, model: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "receipt": {
                "merchant": "",
                "date": "",
                "totalAmount": 0,
                "currency": "RUB",
                "items": [],
                "confidence": 0,
                "error": "bad_json",
                "model": model,
                "engine": engine,
            },
            "warnings": ["Модель вернула не JSON — попробуйте другое фото."],
            "critical_alerts": [
                {
                    "severity": "critical",
                    "code": "receipt_parse_failed",
                    "title": "Ошибка распознавания чека",
                    "detail": "LLM вернул не-JSON",
                    "provider": engine,
                    "fallback": None,
                    "http_status": None,
                    "meta": {"raw": text[:400], "model": model},
                }
            ],
            "meta": {"engine": engine, "model": model, "raw": text[:500]},
        }
    if not isinstance(raw, dict):
        return _parse_vision_json(
            json.dumps({"error": "unreadable", "items": [], "confidence": 0}),
            engine=engine,
            model=model,
        )
    return normalize_receipt_payload(raw, engine=engine, model=model)


def _vision_messages(mime: str, b64: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": RECEIPT_VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]


def _yandex_mime_type(mime: str) -> str:
    m = (mime or "").lower()
    if "png" in m:
        return "PNG"
    if "webp" in m:
        return "WEBP"
    if "pdf" in m:
        return "PDF"
    return "JPEG"


def _extract_yandex_ocr_text(payload: dict[str, Any]) -> str:
    result = payload.get("result") or payload
    ann = result.get("textAnnotation") or {}
    full = str(ann.get("fullText") or "").strip()
    if full:
        return full
    parts: list[str] = []
    for block in ann.get("blocks") or []:
        for line in block.get("lines") or []:
            t = str(line.get("text") or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts).strip()


async def _via_timeweb(mime: str, b64: str) -> dict[str, Any] | None:
    import httpx

    key = (os.getenv("TIMEWEB_AI_API_KEY") or "").strip()
    agent_id = (os.getenv("TIMEWEB_AI_AGENT_ID") or "").strip()
    if not key or not agent_id:
        return None
    base = (os.getenv("TIMEWEB_AI_BASE_URL") or "https://agent.timeweb.cloud").rstrip("/")
    url = f"{base}/api/v1/cloud-ai/agents/{agent_id}/v1/chat/completions"
    body = {
        "model": "gpt-4o-mini",
        "messages": _vision_messages(mime, b64),
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, headers=headers, json=body)
    if res.status_code >= 400:
        return {
            "_failed": True,
            "warnings": [f"Timeweb receipt OCR HTTP {res.status_code}: {res.text[:200]}"],
            "meta": {"engine": "timeweb"},
        }
    payload = res.json()
    text = (payload.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    model = str(payload.get("model") or "timeweb-agent")
    return _parse_vision_json(text, engine="timeweb", model=model)


async def _via_yandex(mime: str, b64: str) -> dict[str, Any] | None:
    """Yandex Vision OCR → YandexGPT JSON. Needs API key + folder id."""
    import httpx

    key = (os.getenv("YANDEX_AI_STUDIO_API_KEY") or "").strip()
    folder = (os.getenv("YANDEX_AI_STUDIO_FOLDER_ID") or "").strip()
    if not key:
        return None
    if not folder:
        return {
            "_failed": True,
            "warnings": ["Yandex: не задан YANDEX_AI_STUDIO_FOLDER_ID"],
            "meta": {"engine": "yandex"},
        }

    ocr_headers = {
        "Authorization": f"Api-Key {key}",
        "Content-Type": "application/json",
        "x-folder-id": folder,
        "x-data-logging-enabled": "false",
    }
    ocr_body = {
        "mimeType": _yandex_mime_type(mime),
        "languageCodes": ["ru", "en"],
        "model": "page",
        "content": b64,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        ocr_res = await client.post(
            "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText",
            headers=ocr_headers,
            json=ocr_body,
        )
    if ocr_res.status_code >= 400:
        return {
            "_failed": True,
            "warnings": [f"Yandex OCR HTTP {ocr_res.status_code}: {ocr_res.text[:200]}"],
            "meta": {"engine": "yandex"},
        }
    ocr_text = _extract_yandex_ocr_text(ocr_res.json())
    if not ocr_text:
        return _parse_vision_json(
            json.dumps(
                {
                    "merchant": "",
                    "date": "",
                    "total": 0,
                    "items": [],
                    "confidence": 0,
                    "error": "unreadable",
                },
                ensure_ascii=False,
            ),
            engine="yandex",
            model="vision-ocr",
        )

    base = (os.getenv("YANDEX_AI_STUDIO_BASE_URL") or "https://llm.api.cloud.yandex.net").rstrip("/")
    model_name = (os.getenv("YANDEX_AI_STUDIO_MODEL") or "yandexgpt-lite").strip()
    model_uri = model_name if model_name.startswith("gpt://") else f"gpt://{folder}/{model_name}"
    chat_headers = {
        "Authorization": f"Api-Key {key}",
        "Content-Type": "application/json",
        "x-folder-id": folder,
    }
    chat_body = {
        "model": model_uri,
        "messages": [{"role": "user", "content": RECEIPT_TEXT_PROMPT + ocr_text[:12000]}],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        chat_res = await client.post(
            f"{base}/v1/chat/completions",
            headers=chat_headers,
            json=chat_body,
        )
    if chat_res.status_code >= 400:
        return {
            "_failed": True,
            "warnings": [f"YandexGPT HTTP {chat_res.status_code}: {chat_res.text[:200]}"],
            "meta": {"engine": "yandex", "ocr_chars": len(ocr_text)},
        }
    payload = chat_res.json()
    text = (payload.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    out = _parse_vision_json(text, engine="yandex", model=str(payload.get("model") or model_uri))
    meta = dict(out.get("meta") or {})
    meta["ocr_chars"] = len(ocr_text)
    meta["pipeline"] = "vision-ocr+yandexgpt"
    out["meta"] = meta
    return out


def _openrouter_vision_models() -> list[str]:
    """Ordered OpenRouter vision models (region-safe: no Google/OpenAI/Anthropic).

    Default trio when env is empty:
      qwen/qwen3-vl-32b-instruct, meta-llama/llama-4-scout, amazon/nova-lite-v1
    Override via OPENROUTER_VISION_MODELS.
    """
    raw = (os.getenv("OPENROUTER_VISION_MODELS") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip() and not m.strip().startswith("#")]
    return [
        "qwen/qwen3-vl-32b-instruct",
        "meta-llama/llama-4-scout",
        "amazon/nova-lite-v1",
    ]


async def _via_openrouter(mime: str, b64: str) -> dict[str, Any] | None:
    import httpx

    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    models = _openrouter_vision_models()
    if not key or not models:
        return None

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://wallet.islanddream.ru",
        "X-Title": "Wallet Receipt OCR",
    }
    last_fail: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in models:
            body = {"model": model, "messages": _vision_messages(mime, b64), "temperature": 0.1}
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=body,
            )
            if res.status_code >= 400:
                last_fail = {
                    "_failed": True,
                    "warnings": [
                        f"OpenRouter receipt OCR HTTP {res.status_code} ({model}): {res.text[:160]}"
                    ],
                    "meta": {"engine": "openrouter", "model": model},
                }
                continue
            payload = res.json()
            text = (payload.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            return _parse_vision_json(
                text, engine="openrouter", model=str(payload.get("model") or model)
            )
    return last_fail


def _humanize_provider_warning(raw: str, *, provider: str) -> str:
    """User-facing text: no raw HTTP JSON in toast."""
    text = (raw or "").strip()
    low = text.lower()
    if "403" in text or "access denied" in low or "security policy" in low:
        return (
            f"{provider} сейчас отклоняет распознавание (доступ ограничен). "
            "Пробуем другой канал."
        )
    if "HTTP " in text:
        return f"{provider}: временный сбой распознавания. Пробуем другой канал."
    return text or f"{provider}: сбой распознавания."


async def parse_receipt_image(data: bytes, mime: str) -> dict[str, Any]:
    """Receipt parse with provider fallback.

    Temporary soak order (all three are primary fallbacks):
      Yandex → Timeweb → OpenRouter (Qwen VL → Llama 4 Scout → Nova Lite)
    """
    from critical_alerts import (
        openrouter_alert_from_error,
        remember_openrouter_failure,
        remember_openrouter_ok,
    )

    b64 = base64.b64encode(data).decode("ascii")
    critical: list[dict[str, Any]] = []
    soft: list[str] = []

    async def _try_timeweb() -> dict[str, Any] | None:
        tw = await _via_timeweb(mime, b64)
        if tw is None:
            return None
        if tw.pop("_failed", False):
            raw = (tw.get("warnings") or ["Timeweb receipt OCR failed"])[0]
            soft.append(_humanize_provider_warning(raw, provider="Timeweb"))
            return None
        return tw

    async def _try_yandex() -> dict[str, Any] | None:
        ya = await _via_yandex(mime, b64)
        if ya is None:
            return None
        if ya.pop("_failed", False):
            raw = (ya.get("warnings") or ["Yandex receipt OCR failed"])[0]
            soft.append(_humanize_provider_warning(raw, provider="Яндекс"))
            return None
        return ya

    async def _try_openrouter() -> dict[str, Any] | None:
        orr = await _via_openrouter(mime, b64)
        if orr is None:
            return None
        if orr.pop("_failed", False):
            raw = (orr.get("warnings") or ["OpenRouter receipt OCR failed"])[0]
            soft.append(_humanize_provider_warning(raw, provider="OpenRouter"))
            status = None
            m = re.search(r"HTTP\s+(\d+)", raw)
            if m:
                try:
                    status = int(m.group(1))
                except ValueError:
                    status = None
            remember_openrouter_failure(raw, status)
            critical.append(
                openrouter_alert_from_error(
                    raw,
                    context="OCR чека",
                    fallback="Yandex / Timeweb",
                    http_status=status,
                )
            )
            return None
        remember_openrouter_ok()
        return orr

    engines = [_try_yandex, _try_timeweb, _try_openrouter]
    for attempt in engines:
        out = await attempt()
        if out is None:
            continue
        result = dict(out)
        if result.get("ok") and (result.get("receipt") or {}).get("items"):
            result["critical_alerts"] = list(result.get("critical_alerts") or []) + critical
            result["warnings"] = [
                w
                for w in (result.get("warnings") or [])
                if "Пробуем другой канал" not in w and "OpenRouter" not in w and "HTTP " not in w
            ]
            return result
        result["critical_alerts"] = list(result.get("critical_alerts") or []) + critical
        if soft:
            existing = list(result.get("warnings") or [])
            result["warnings"] = existing or soft
        return result

    if not soft and not critical:
        soft.append(
            "Распознавание чека недоступно: нет YANDEX_AI_STUDIO_* / TIMEWEB_AI_* / OPENROUTER"
        )
        critical.append(
            {
                "severity": "critical",
                "code": "receipt_parse_failed",
                "title": "Ошибка распознавания чека",
                "detail": soft[0],
                "provider": "none",
                "fallback": None,
                "http_status": None,
            }
        )
    return {
        "ok": False,
        "receipt": {
            "merchant": "",
            "date": "",
            "totalAmount": 0,
            "currency": "RUB",
            "items": [],
            "confidence": 0,
            "error": "provider_unavailable",
            "model": "",
            "engine": "none",
        },
        "warnings": soft,
        "critical_alerts": critical,
        "meta": {"engine": "none"},
    }


async def parse_receipt_upload(data: bytes, filename: str, content_type: str) -> dict[str, Any]:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if not (
        ctype.startswith("image/")
        or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"))
    ):
        raise ValueError(
            "Для чека нужно изображение (PNG/JPG/WebP). CSV и PDF выписки — через кнопку импорта."
        )

    mime = ctype if ctype.startswith("image/") else "image/jpeg"
    if name.endswith(".png") or mime == "image/png":
        mime = "image/png"
    elif name.endswith(".webp") or mime == "image/webp":
        mime = "image/webp"
    elif name.endswith((".heic", ".heif")) or "heic" in mime or "heif" in mime:
        mime = "image/heic"
    elif name.endswith((".jpg", ".jpeg")) or mime in ("image/jpeg", "image/jpg"):
        mime = "image/jpeg"
    return await parse_receipt_image(data, mime)
