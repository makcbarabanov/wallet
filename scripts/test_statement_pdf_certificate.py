#!/usr/bin/env python3
"""Regression tests for T-Bank PDF certificate → normalize_row (no separate pipeline)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from statement_parse import (  # noqa: E402
    MONEY_RE,
    _extract_money_tokens,
    _rows_from_tbank_certificate,
    _rows_from_text_lines,
    parse_pdf_bytes,
)


def test_money_re_rejects_card_tail():
    line = "26.07.2026 26.07.2026 -349.97 ₽ -349.97 ₽ Оплата в PYATEROCHKA 0507"
    tokens = _extract_money_tokens(line)
    amounts = [round(a, 2) for a, _, _ in tokens]
    assert -349.97 in amounts
    assert 7.0 not in amounts and 7 not in amounts
    assert 50.7 not in [abs(x) for x in amounts]


def test_pyaterochka_card_not_amount():
    lines = [
        "Справка о движении средств",
        "26.07.2026 26.07.2026 -349.97 ₽ -349.97 ₽ Оплата в PYATEROCHKA 0507",
        "18:13 18:28 7314 SANKT-PETERBU RUS",
    ]
    rows, diag = _rows_from_tbank_certificate(lines)
    assert len(rows) == 1
    r = rows[0]
    assert abs(r["amount"] - (-349.97)) < 0.001
    assert r["amount"] < 0
    assert "Пятёрочка" in r["store"] or "PYATEROCHKA" in r["store"].upper()
    assert "0507" in (r["bank"].get("card") or "") or "0507" in (r.get("comment") or "")
    assert diag["bad_amount_7"] == 0


def test_income_plus_5555():
    lines = [
        "20.07.2026 20.07.2026 +5 555.00 ₽ +5 555.00 ₽ Пополнение. Сбербанк 0507",
        "12:54 12:54",
    ]
    rows, _ = _rows_from_tbank_certificate(lines)
    assert len(rows) == 1
    assert rows[0]["amount"] > 0
    assert abs(rows[0]["amount"] - 5555.0) < 0.01
    assert rows[0]["kind"] == "unclear"  # income / unclear in shared normalize_row


def test_yandex_continuation_merchant():
    lines = [
        "26.07.2026 26.07.2026 -1.47 ₽ -1.47 ₽ Оплата в 0507",
        "16:39 17:22 YANDEX*7372*OBLAKO",
        "Moskva RUS",
    ]
    rows, _ = _rows_from_tbank_certificate(lines)
    assert len(rows) == 1
    assert abs(rows[0]["amount"] - (-1.47)) < 0.001
    assert "YANDEX" in rows[0]["store"].upper() or "Yandex" in rows[0]["store"]


def test_refuse_line_without_money_pattern():
    lines = [
        "26.07.2026 какая-то строка без денег 0507",
        "хвост",
    ]
    rows, diag = _rows_from_tbank_certificate(lines)
    assert rows == []


def test_legacy_text_lines_no_last_integer():
    """Generic path must not pick card 7 as amount."""
    line = "26.07.2026 покупка -349.97 ₽ PYATEROCHKA 0507"
    rows = _rows_from_text_lines([line])
    assert len(rows) == 1
    assert abs(rows[0]["amount"] - (-349.97)) < 0.001


def test_inbox_pdf_vs_quality_gate():
    pdf_path = Path(__file__).resolve().parents[1] / "inbox" / "Справка о движении средств(1).pdf"
    if not pdf_path.is_file():
        print("SKIP inbox PDF missing")
        return
    data = pdf_path.read_bytes()
    out = parse_pdf_bytes(data)
    rows = out["rows"]
    meta = out["meta"]
    assert len(rows) >= 100, f"expected many ops, got {len(rows)}"
    bad7 = sum(1 for r in rows if abs(abs(float(r["amount"])) - 7.0) < 1e-9)
    assert bad7 == 0, f"card-tail amounts still present: {bad7}"
    assert all(r.get("date") for r in rows)
    assert all(abs(float(r["amount"])) >= 0.01 for r in rows)
    # PYATEROCHKA / Пятёрочка -349.97 present
    hit = [
        r
        for r in rows
        if abs(abs(float(r["amount"])) - 349.97) < 0.01 and float(r["amount"]) < 0
    ]
    assert hit, "expected -349.97 expense"
    assert meta.get("diagnostics", {}).get("format") == "tbank_certificate"


def test_inbox_pdf_day_amount_overlap_csv():
    """Same period: PDF day+|amount| should largely overlap CSV expenses."""
    import csv
    import re

    root = Path(__file__).resolve().parents[1]
    pdf_path = root / "inbox" / "Справка о движении средств(1).pdf"
    csv_path = root / "inbox" / "Operations Wed Jul 01 2026-Sun Jul 26 2026.csv"
    if not pdf_path.is_file() or not csv_path.is_file():
        print("SKIP inbox files missing")
        return

    def day(d: str) -> str:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(d))
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", str(d))
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return ""

    pdf_rows = parse_pdf_bytes(pdf_path.read_bytes())["rows"]
    pdf_keys = {(day(r["date"]), round(abs(float(r["amount"])), 2)) for r in pdf_rows}

    csv_keys = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if (row.get("Статус") or "").strip().upper() not in ("OK", ""):
                continue
            raw = (row.get("Сумма операции") or "").replace(" ", "").replace(",", ".")
            try:
                amt = float(raw)
            except ValueError:
                continue
            if not (amt < 0):
                continue
            csv_keys.add((day(row.get("Дата операции") or ""), round(abs(amt), 2)))

    overlap = pdf_keys & csv_keys
    # Criterion: most CSV expenses visible in PDF by date+amount
    assert len(overlap) >= 0.85 * len(csv_keys), (
        f"overlap {len(overlap)}/{len(csv_keys)} pdf={len(pdf_keys)}"
    )


if __name__ == "__main__":
    test_money_re_rejects_card_tail()
    test_pyaterochka_card_not_amount()
    test_income_plus_5555()
    test_yandex_continuation_merchant()
    test_refuse_line_without_money_pattern()
    test_legacy_text_lines_no_last_integer()
    test_inbox_pdf_vs_quality_gate()
    test_inbox_pdf_day_amount_overlap_csv()
    print("OK test_statement_pdf_certificate")
