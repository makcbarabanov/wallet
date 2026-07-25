"""Smoke tests for CSV parsing and grouping status logic."""

from app.services.csv_parser import parse_statement


SAMPLE = """Дата;Магазин;Сумма;Комментарий
15.01.2026;Яндекс Такси;-450,00;Поездка
16.01.2026;Перекрёсток;-1 850,50;
"""


TBANK_SAMPLE = """Дата операции;Дата платежа;Номер карты;Статус;Сумма операции;Валюта операции;Сумма платежа;Валюта платежа;Кэшбэк;Категория;MCC;Описание;Бонусы (включая кэшбэк);Округление на инвесткопилку;Сумма операции с округлением
"12.07.2026 20:06:27";"12.07.2026";"*0507";"OK";"-89,99";"RUB";"-89,99";"RUB";"";"Супермаркеты";"5411";"Семишагофф";"0,00";"0,00";"-89,99"
"12.07.2026 19:40:13";"12.07.2026";"*0507";"OK";"-200,00";"RUB";"-200,00";"RUB";"";"Мобильная связь";"";"Билайн +7 960 233-48-67";"0,00";"0,00";"-200,00"
"11.07.2026 23:26:55";"12.07.2026";"*0507";"FAILED";"-795,96";"RUB";"-795,96";"RUB";"";"Супермаркеты";"5411";"Лента";"0,00";"0,00";"-795,96"
"11.07.2026 15:42:26";"11.07.2026";"*0507";"OK";"-95,00";"RUB";"-95,00";"RUB";"";"Местный транспорт";"4111";"Метро Санкт-Петербург";"0,00";"0,00";"-95,00"
"11.07.2026 15:16:00";"11.07.2026";"*0507";"OK";"-95,00";"RUB";"-95,00";"RUB";"";"Местный транспорт";"4111";"Метро Санкт-Петербург";"0,00";"0,00";"-95,00"
"""


def test_parse_basic():
    rows = parse_statement(SAMPLE.encode("utf-8"))
    assert len(rows) == 2
    assert rows[0].store == "Яндекс Такси"
    assert rows[0].amount == -450.0
    assert rows[1].amount == -1850.50


def test_parse_comma_csv():
    data = "Дата,Магазин,Сумма,Комментарий\n01.02.2026,Ozon,-100.00,\n"
    rows = parse_statement(data)
    assert rows[0].store == "Ozon"
    assert rows[0].amount == -100.0


def test_parse_tbank_native():
    rows = parse_statement(TBANK_SAMPLE.encode("utf-8"))
    # FAILED status row skipped → 4 OK rows
    assert len(rows) == 4
    assert rows[0].store == "Семишагофф"
    assert rows[0].date.isoformat() == "2026-07-12"
    assert rows[0].amount == -89.99
    assert "Супермаркеты" in rows[0].comment
    assert "*0507" in rows[0].comment

    # Same store+amount+comment same day but different time → distinct fingerprints
    metro = [r for r in rows if r.store == "Метро Санкт-Петербург"]
    assert len(metro) == 2
    assert metro[0].fingerprint != metro[1].fingerprint
