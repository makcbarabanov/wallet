"""CSV parser for T-Bank card statements.

Supports two formats:

1. Simple: Дата | Магазин | Сумма | Комментарий
2. Native T-Bank export:
   Дата операции | Описание | Сумма операции | Категория | Номер карты | Статус | …

Amounts may use comma as decimal separator; dates in DD.MM.YYYY,
DD.MM.YYYY HH:MM:SS, or ISO.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import BinaryIO, TextIO


SIMPLE_REQUIRED = {"дата", "магазин", "сумма"}
TBANK_MARKERS = {"дата операции", "описание", "сумма операции"}


@dataclass
class ParsedRow:
    date: date
    store: str
    amount: float
    comment: str
    fingerprint: str


class CsvParseError(ValueError):
    """Raised when the uploaded file cannot be parsed as a statement."""


def _normalize_header(raw: str) -> str:
    return raw.strip().lower().lstrip("\ufeff")


def _detect_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        return dialect.delimiter
    except csv.Error:
        # T-Bank exports often use `;`
        return ";" if sample.count(";") >= sample.count(",") else ","


def _parse_date(value: str) -> date:
    """Parse date; if datetime is present, return the date part only."""
    value = value.strip()
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise CsvParseError(f"Не удалось разобрать дату: «{value}»")


def _parse_amount(value: str) -> float:
    """Parse amounts like «1 234,56», «-4200.00», «1,234.56»."""
    raw = value.strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        raise CsvParseError("Пустая сумма")

    # European: 1.234,56 → remove thousands dots, comma → dot
    if re.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    # Mixed: 1,234.56
    elif re.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$", raw):
        raw = raw.replace(",", "")
    # Simple comma decimal: 1234,56
    elif "," in raw and "." not in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except ValueError as exc:
        raise CsvParseError(f"Не удалось разобрать сумму: «{value}»") from exc


def _fingerprint(*parts: str) -> str:
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _open_text(source: BinaryIO | TextIO | bytes | str) -> TextIO:
    if isinstance(source, bytes):
        # Try utf-8-sig first (BOM), then cp1251 (common for RU bank exports)
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                return io.StringIO(source.decode(encoding))
            except UnicodeDecodeError:
                continue
        raise CsvParseError("Не удалось определить кодировку файла")
    if isinstance(source, str):
        return io.StringIO(source)
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, bytes):
            return _open_text(data)
        return io.StringIO(data)
    raise CsvParseError("Неподдерживаемый источник CSV")


def _is_tbank(header_map: dict[str, str]) -> bool:
    return TBANK_MARKERS.issubset(header_map.keys())


def _build_tbank_comment(category: str, card: str) -> str:
    parts: list[str] = []
    if category:
        parts.append(category)
    if card:
        parts.append(f"карта {card}")
    return " · ".join(parts)


def parse_statement(source: BinaryIO | TextIO | bytes | str) -> list[ParsedRow]:
    """
    Parse a T-Bank CSV statement into structured rows.

    Returns only valid data rows; raises CsvParseError on structural problems.
    """
    text = _open_text(source)
    sample = text.read(4096)
    text.seek(0)
    delimiter = _detect_delimiter(sample)

    reader = csv.DictReader(text, delimiter=delimiter)
    if not reader.fieldnames:
        raise CsvParseError("CSV не содержит заголовков")

    header_map: dict[str, str] = {}
    for original in reader.fieldnames:
        if original is None:
            continue
        norm = _normalize_header(original)
        header_map[norm] = original

    if _is_tbank(header_map):
        return _parse_tbank(reader, header_map)

    missing = SIMPLE_REQUIRED - set(header_map.keys())
    if missing:
        raise CsvParseError(
            f"В CSV нет обязательных колонок: {', '.join(sorted(missing))}. "
            f"Найдены: {', '.join(reader.fieldnames)}"
        )

    return _parse_simple(reader, header_map)


def _parse_simple(
    reader: csv.DictReader, header_map: dict[str, str]
) -> list[ParsedRow]:
    date_col = header_map["дата"]
    store_col = header_map["магазин"]
    amount_col = header_map["сумма"]
    comment_col = header_map.get("комментарий") or header_map.get("comment")

    rows: list[ParsedRow] = []
    for idx, raw in enumerate(reader, start=2):
        date_val = (raw.get(date_col) or "").strip()
        store_val = (raw.get(store_col) or "").strip()
        amount_val = (raw.get(amount_col) or "").strip()
        comment_val = (raw.get(comment_col) or "").strip() if comment_col else ""

        if not date_val and not store_val and not amount_val:
            continue

        if not date_val or not store_val or not amount_val:
            raise CsvParseError(
                f"Строка {idx}: заполните Дата, Магазин и Сумма "
                f"(дата=«{date_val}», магазин=«{store_val}», сумма=«{amount_val}»)"
            )

        parsed_date = _parse_date(date_val)
        amount = _parse_amount(amount_val)
        store = store_val

        rows.append(
            ParsedRow(
                date=parsed_date,
                store=store,
                amount=amount,
                comment=comment_val,
                fingerprint=_fingerprint(
                    parsed_date.isoformat(),
                    store,
                    f"{amount:.2f}",
                    comment_val,
                ),
            )
        )

    if not rows:
        raise CsvParseError("В файле нет ни одной операции")

    return rows


def _parse_tbank(
    reader: csv.DictReader, header_map: dict[str, str]
) -> list[ParsedRow]:
    date_col = header_map["дата операции"]
    store_col = header_map["описание"]
    amount_col = header_map["сумма операции"]
    category_col = header_map.get("категория")
    card_col = header_map.get("номер карты")
    status_col = header_map.get("статус")

    rows: list[ParsedRow] = []
    for idx, raw in enumerate(reader, start=2):
        date_val = (raw.get(date_col) or "").strip()
        store_val = (raw.get(store_col) or "").strip()
        amount_val = (raw.get(amount_col) or "").strip()

        if not date_val and not store_val and not amount_val:
            continue

        if status_col:
            status = (raw.get(status_col) or "").strip().upper()
            if status and status != "OK":
                continue

        if not date_val or not store_val or not amount_val:
            raise CsvParseError(
                f"Строка {idx}: заполните Дата операции, Описание и Сумма операции "
                f"(дата=«{date_val}», описание=«{store_val}», сумма=«{amount_val}»)"
            )

        parsed_date = _parse_date(date_val)
        amount = _parse_amount(amount_val)
        store = store_val

        category = (raw.get(category_col) or "").strip() if category_col else ""
        card = (raw.get(card_col) or "").strip() if card_col else ""
        comment_val = _build_tbank_comment(category, card)

        # Include full datetime string so same-day duplicates don't collide
        rows.append(
            ParsedRow(
                date=parsed_date,
                store=store,
                amount=amount,
                comment=comment_val,
                fingerprint=_fingerprint(
                    date_val,
                    store,
                    f"{amount:.2f}",
                    comment_val,
                ),
            )
        )

    if not rows:
        raise CsvParseError("В файле нет ни одной операции")

    return rows
