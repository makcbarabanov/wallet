#!/usr/bin/env python3
"""Unit tests for receipt_parse.normalize_receipt_payload + line math (no LLM)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from receipt_parse import (  # noqa: E402
    normalize_receipt_line_item,
    normalize_receipt_payload,
)


def test_three_items():
    out = normalize_receipt_payload(
        {
            "merchant": "Пятёрочка",
            "date": "25.07.2026",
            "total": 2350,
            "confidence": 0.9,
            "items": [
                {"name": "Молоко", "amount": 1000, "category": "Продукты", "subcategory": ""},
                {"name": "Хлеб", "amount": 1100, "category": "Продукты"},
                {"name": "Кофе", "amount": 250, "category": "Напитки", "subcategory": "Кофе"},
            ],
        },
        engine="test",
        model="unit",
    )
    assert out["ok"] is True
    assert out["receipt"]["merchant"] == "Пятёрочка"
    assert out["receipt"]["date"].startswith("2026-07-25")
    assert len(out["receipt"]["items"]) == 3
    assert abs(out["receipt"]["totalAmount"] - 2350) < 0.01
    assert out["receipt"]["items"][2]["subcategory"] == "Кофе"


def test_per_item_categories_not_store_level():
    """One receipt, different categories per line — never collapse to merchant category."""
    out = normalize_receipt_payload(
        {
            "merchant": "ООО Стройторговля",
            "date": "24.07.2026",
            "total": 1082,
            "confidence": 0.85,
            "items": [
                {"name": "Изолента", "amount": 37, "category": "Материалы", "subcategory": "Расходники"},
                {"name": "Выключатель", "amount": 208, "category": "Материалы", "subcategory": "Электрика"},
                {"name": "Тарелка опорная", "amount": 360, "category": "Инструмент / оборудование", "subcategory": ""},
            ],
        },
        engine="test",
        model="unit",
    )
    cats = {i["category"] for i in out["receipt"]["items"]}
    assert len(out["receipt"]["items"]) == 3
    assert "Материалы" in cats
    assert "Инструмент / оборудование" in cats
    assert out["receipt"]["items"][0]["subcategory"] == "Расходники"


def test_unreadable():
    out = normalize_receipt_payload(
        {"error": "unreadable", "items": [], "confidence": 0},
        engine="test",
        model="unit",
    )
    assert out["ok"] is False
    assert out["critical_alerts"]
    assert out["critical_alerts"][0]["code"] == "receipt_parse_failed"
    assert "Не удалось" in (out["warnings"][0] if out["warnings"] else "")


def test_low_confidence_keeps_items_as_proposal():
    out = normalize_receipt_payload(
        {
            "merchant": "Аптека",
            "date": "2026-07-20",
            "total": 390,
            "confidence": 0.3,
            "items": [{"name": "Нурофен", "amount": 390, "category": "Здоровье"}],
        },
        engine="test",
        model="unit",
    )
    assert out["ok"] is True
    assert out["receipt"]["items"]
    assert any("уверенност" in w.lower() for w in out["warnings"])


def test_magnet_chili_correct():
    """Магнит: 1169.91 × 0.084 ≈ 98.27 — ok, unit=кг."""
    it = normalize_receipt_line_item(
        {
            "name": "ПЕРЕЦ ЧИЛИ КРАСНЫЙ 1КГ",
            "qty": 0.084,
            "price": 1169.91,
            "amount": 98.27,
            "category": "Продукты",
        }
    )
    assert it is not None
    assert it["amountStatus"] == "ok"
    assert it["unit"] == "кг"
    assert abs(it["qty"] - 0.084) < 1e-6
    assert abs(it["price"] - 1169.91) < 0.01
    assert abs(it["amount"] - 98.27) < 0.01


def test_magnet_chili_confused_never_autofix():
    """If amount was filled with unit price — doubt, do NOT rewrite amount."""
    it = normalize_receipt_line_item(
        {
            "name": "ПЕРЕЦ ЧИЛИ КРАСНЫЙ 1КГ",
            "qty": 0.084,
            "price": 1169.91,
            "amount": 1169.91,
            "category": "Продукты",
        }
    )
    assert it["amountStatus"] == "doubt"
    assert it["amountDoubt"]["kind"] == "unit_price_as_amount"
    assert abs(it["amount"] - 1169.91) < 0.01  # invariant: not auto-fixed
    cand0 = it["amountDoubt"]["candidates"][0]
    assert abs(cand0["amount"] - 98.27) < 0.02
    assert cand0["unit"] == "кг"


def test_piece_item():
    it = normalize_receipt_line_item(
        {"name": "Хлеб белый", "qty": 1, "price": 129.99, "amount": 129.99}
    )
    assert it["amountStatus"] == "ok"
    assert it["unit"] == "шт"
    assert abs(it["qty"] - 1) < 1e-6
    assert abs(it["price"] - it["amount"]) < 0.01


def test_multi_piece():
    it = normalize_receipt_line_item(
        {"name": "Яйцо С1", "qty": 3, "price": 50, "amount": 150}
    )
    assert it["amountStatus"] == "ok"
    assert abs(it["amount"] - 150) < 0.01


def test_conflict_asks():
    it = normalize_receipt_line_item(
        {"name": "Товар", "qty": 2, "price": 50, "amount": 120}
    )
    assert it["amountStatus"] == "doubt"
    assert it["amountDoubt"]["kind"] == "conflict"
    assert abs(it["amount"] - 120) < 0.01  # not silently changed to 100


if __name__ == "__main__":
    test_three_items()
    test_per_item_categories_not_store_level()
    test_unreadable()
    test_low_confidence_keeps_items_as_proposal()
    test_magnet_chili_correct()
    test_magnet_chili_confused_never_autofix()
    test_piece_item()
    test_multi_piece()
    test_conflict_asks()
    print("receipt_parse OK")
    print("- 3 items → structured receipt")
    print("- per-item categories (not store-level)")
    print("- magnet chili ok / confused→doubt (no autofix)")
    print("- piece / multi / conflict")
