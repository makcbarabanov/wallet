"""Read-only plan pulse for Valet (mirrors HTML analyzePlan / buildPlanTree)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any


RIGID_DEFAULT = {
    "Жильё",
    "Продукты",
    "Здоровье",
    "Связь",
    "Транспорт",
    "Платежи",
}


def _current_ym(today: date | None = None) -> str:
    t = today or date.today()
    return f"{t.year:04d}-{t.month:02d}"


def _days_in_month(ym: str) -> int:
    y, m = map(int, ym.split("-"))
    if m == 12:
        return (date(y + 1, 1, 1) - date(y, m, 1)).days
    return (date(y, m + 1, 1) - date(y, m, 1)).days


def _plan_day_context(ym: str, today: date | None = None) -> dict[str, int]:
    t = today or date.today()
    y, m = map(int, ym.split("-"))
    days = _days_in_month(ym)
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    if t < start:
        day_n = 0
    elif t >= end:
        day_n = days
    else:
        day_n = t.day
    return {"y": y, "m": m, "days": days, "dayN": day_n}


def _parse_row_date_parts(date_str: str) -> dict[str, Any] | None:
    s = str(date_str or "").strip()
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2,4})", s)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(yy) if len(yy) == 4 else 2000 + int(yy)
        return {"day": dd, "month": mm, "year": y, "key": f"{y:04d}-{mm:02d}"}
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return {"day": dd, "month": mm, "year": y, "key": f"{y:04d}-{mm:02d}"}
    return None


def _split_compound(category: str, expense: str = "") -> tuple[str, str]:
    cat = str(category or "").strip()
    exp = str(expense or "").strip()
    m = re.match(r"^транспорт\s*[·•\u00b7\-–—]\s*(.+)$", cat, re.I)
    if m:
        return "Транспорт", m.group(1).strip() or exp
    return cat, exp


def _get_envelope(category: str, envelopes: dict[str, Any]) -> str:
    m = envelopes.get(category)
    if m in ("rigid", "soft"):
        return m
    return "rigid" if category in RIGID_DEFAULT else "soft"


def _get_pace(category: str, modes: dict[str, Any]) -> str:
    m = modes.get(category)
    if m in ("daily", "monthly"):
        return m
    return "daily" if category == "Продукты" else "monthly"


def _pace_class(fact: float, plan_ref: float) -> str:
    if plan_ref <= 0 and fact <= 0:
        return "pace-zero"
    if plan_ref <= 0 and fact > 0:
        return "pace-bad"
    ratio = fact / plan_ref
    if ratio <= 1:
        return "pace-ok"
    if ratio <= 1.10:
        return "pace-warn"
    return "pace-bad"


def _plan_to_date(plan: float, pace: str, day_n: int, days: int) -> float:
    if pace == "daily":
        return (plan / max(days, 1)) * day_n if day_n else 0.0
    return plan


def _delta_label(delta: int) -> str:
    if delta > 0:
        return "запас"
    if delta < 0:
        return "перерасход"
    return "по темпу"


def _clean_alert(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize alert payload for UI/LLM (no fake +899%)."""
    out = dict(row)
    ptd = float(out.get("plan_to_date") or 0)
    pct = out.get("pct_of_pace")
    if ptd <= 0 or pct is None or int(pct) >= 900:
        out["overspend_pct"] = None
        out["unplanned"] = True
    else:
        out["overspend_pct"] = max(0, int(pct) - 100)
        out["unplanned"] = False
    return out


def _is_over_row(row: dict[str, Any]) -> bool:
    return row.get("cls") in ("pace-warn", "pace-bad") and float(row.get("overspend_rub") or 0) > 0



def _watch_set(raw: Any) -> set[str]:
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    if isinstance(raw, dict):
        return {str(k).strip() for k, v in raw.items() if v and str(k).strip()}
    return set()


def plan_summary(payload: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Build a compact North-Star pulse. Read-only; no DB writes."""
    t = today or date.today()
    ym = str(payload.get("planMonth") or "") or _current_ym(t)
    ctx = _plan_day_context(ym, t)
    plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
    block = plans.get(ym) if isinstance(plans, dict) else None
    lines = block.get("lines") if isinstance(block, dict) and isinstance(block.get("lines"), list) else []
    envelopes = payload.get("planEnvelopes") if isinstance(payload.get("planEnvelopes"), dict) else {}
    pace_modes = payload.get("planPaceModes") if isinstance(payload.get("planPaceModes"), dict) else {}

    by_cat: dict[str, float] = {}
    by_exp: dict[tuple[str, str], float] = {}
    for r in payload.get("personal") or []:
        if not isinstance(r, dict):
            continue
        parts = _parse_row_date_parts(str(r.get("date") or ""))
        if not parts or parts["key"] != ym:
            continue
        if ctx["dayN"] > 0 and parts["day"] > ctx["dayN"]:
            continue
        cat, exp = _split_compound(str(r.get("category") or ""), str(r.get("expense") or ""))
        if not cat or re.match(r"^транспорт\s*[·•]", cat, re.I):
            continue
        cost = float(r.get("cost") or 0)
        by_cat[cat] = by_cat.get(cat, 0.0) + cost
        if exp:
            key = (cat, exp)
            by_exp[key] = by_exp.get(key, 0.0) + cost

    plan_by_cat: dict[str, float] = {}
    plan_by_exp: dict[tuple[str, str], float] = {}
    for line in lines:
        if not isinstance(line, dict):
            continue
        cat, exp = _split_compound(str(line.get("category") or ""), str(line.get("expense") or ""))
        if not cat or re.match(r"^транспорт\s*[·•]", cat, re.I):
            continue
        amt = float(line.get("amount") or 0)
        if amt <= 0:
            continue
        kind = line.get("kind")
        if kind == "expense" or (exp and kind != "category"):
            plan_by_cat[cat] = plan_by_cat.get(cat, 0.0) + amt
            if exp:
                plan_by_exp[(cat, exp)] = plan_by_exp.get((cat, exp), 0.0) + amt
        elif kind == "category":
            has_exp = any(
                isinstance(l, dict)
                and l.get("kind") == "expense"
                and _split_compound(str(l.get("category") or ""), "")[0] == cat
                and float(l.get("amount") or 0) > 0
                for l in lines
            )
            if not has_exp:
                plan_by_cat[cat] = plan_by_cat.get(cat, 0.0) + amt

    cats = set(plan_by_cat) | set(by_cat)
    for c in payload.get("categories") or []:
        if isinstance(c, dict) and c.get("wallet") == "personal":
            name = str(c.get("name") or "")
            if name and name not in ("Разобрать", "Кафе и рестораны"):
                cats.add(name)

    groups = []
    total_plan = 0.0
    total_fact = 0.0
    buffer = 0.0
    overspend = 0.0
    remaining_planned = 0.0
    day_n = ctx["dayN"]
    days = max(ctx["days"], 1)

    for cat in sorted(cats, key=lambda x: x.lower()):
        if not cat or cat in ("Разобрать",):
            continue
        plan = float(plan_by_cat.get(cat, 0.0))
        fact = float(by_cat.get(cat, 0.0))
        if plan <= 0 and fact <= 0:
            continue
        pace = _get_pace(cat, pace_modes)
        plan_to_date = _plan_to_date(plan, pace, day_n, days)
        cls = _pace_class(fact, plan_to_date)
        pct = round((fact / plan_to_date) * 100) if plan_to_date > 0 else (None if fact <= 0 else 999)
        env = _get_envelope(cat, envelopes)
        diff = plan - fact
        if diff >= 0:
            if env == "soft":
                buffer += diff
            remaining_planned += diff
        else:
            overspend += -diff
        total_plan += plan
        total_fact += fact
        delta = int(round(plan_to_date - fact))
        groups.append(
            {
                "category": cat,
                "expense": None,
                "envelope": env,
                "pace": pace,
                "plan": round(plan),
                "fact_to_date": round(fact),
                "plan_to_date": round(plan_to_date),
                "pct_of_pace": pct,
                "cls": cls,
                "delta_vs_pace": delta,
                "delta_label": _delta_label(delta),
                "overspend_rub": max(0, round(fact - plan_to_date)),
                "overspend_pct": max(0, (pct or 0) - 100) if pct is not None else None,
            }
        )

    # expense-level rows (for warnings / watch)
    exp_rows: list[dict[str, Any]] = []
    exp_keys = set(plan_by_exp) | set(by_exp)
    for cat, exp in sorted(exp_keys, key=lambda x: (x[0].lower(), x[1].lower())):
        plan = float(plan_by_exp.get((cat, exp), 0.0))
        fact = float(by_exp.get((cat, exp), 0.0))
        if plan <= 0 and fact <= 0:
            continue
        pace = _get_pace(cat, pace_modes)
        plan_to_date = _plan_to_date(plan, pace, day_n, days)
        cls = _pace_class(fact, plan_to_date)
        pct = round((fact / plan_to_date) * 100) if plan_to_date > 0 else (None if fact <= 0 else 999)
        delta = int(round(plan_to_date - fact))
        exp_rows.append(
            {
                "category": cat,
                "expense": exp,
                "pace": pace,
                "plan": round(plan),
                "fact_to_date": round(fact),
                "plan_to_date": round(plan_to_date),
                "pct_of_pace": pct,
                "cls": cls,
                "delta_vs_pace": delta,
                "delta_label": _delta_label(delta),
                "overspend_rub": max(0, round(fact - plan_to_date)),
                "overspend_pct": max(0, (pct or 0) - 100) if pct is not None else None,
            }
        )

    # Auto warnings = same truth as plan table pace on CATEGORIES.
    # Expense detail only if that expense has its own plan and is over.
    # Never flag "Жильё — Вертикаль" when category Жильё is on plan.
    overspend_alerts: list[dict[str, Any]] = []
    for cat_row in groups:
        if not _is_over_row(cat_row):
            continue
        cat = cat_row["category"]
        child_overs = [
            e
            for e in exp_rows
            if e["category"] == cat and float(e.get("plan") or 0) > 0 and _is_over_row(e)
        ]
        if child_overs:
            for e in child_overs:
                overspend_alerts.append(_clean_alert(e))
        else:
            overspend_alerts.append(_clean_alert({**cat_row, "expense": None}))
    overspend_alerts.sort(key=lambda g: -float(g.get("overspend_rub") or 0))

    watch_cats = _watch_set(payload.get("valetWatchCategories"))
    watch_exps = _watch_set(payload.get("valetWatchExpenses"))
    watched: list[dict[str, Any]] = []
    for row in groups:
        if row["category"] in watch_cats:
            watched.append(row)
    for row in exp_rows:
        key = f"{row['category']}::{row['expense']}"
        if key in watch_exps:
            watched.append(row)
    seen: set[str] = set()
    watched_unique: list[dict[str, Any]] = []
    for row in watched:
        k = f"{row['category']}::{row.get('expense') or ''}"
        if k in seen:
            continue
        seen.add(k)
        watched_unique.append(row)

    # cash / reserves / free
    accounts = [a for a in (payload.get("accounts") or []) if isinstance(a, dict)]
    cash_accounts = [
        {"id": a.get("id"), "name": str(a.get("name") or "Счёт"), "amount": round(float(a.get("amount") or 0))}
        for a in accounts
    ]
    kassa = round(sum(a["amount"] for a in cash_accounts))
    reserves_raw = [r for r in (payload.get("cashReserves") or []) if isinstance(r, dict)]
    cash_reserves = [
        {
            "id": str(r.get("id") or ""),
            "title": str(r.get("title") or "").strip() or "Без названия",
            "amount": round(float(r.get("amount") or 0)),
        }
        for r in reserves_raw
        if float(r.get("amount") or 0) != 0 or str(r.get("title") or "").strip()
    ]
    reserved = round(sum(r["amount"] for r in cash_reserves))
    free_money = round(kassa - reserved)
    remaining = round(remaining_planned)
    free_after_plan = round(free_money - remaining)

    # data gaps
    last_personal = None
    for r in payload.get("personal") or []:
        if not isinstance(r, dict):
            continue
        parts = _parse_row_date_parts(str(r.get("date") or ""))
        if not parts:
            continue
        try:
            d = date(parts["year"], parts["month"], parts["day"])
        except ValueError:
            continue
        if last_personal is None or d > last_personal:
            last_personal = d
    days_since_expense = (t - last_personal).days if last_personal else None
    imported_raw = [o for o in (payload.get("importedOps") or []) if isinstance(o, dict)]
    confirmed = {str(x) for x in (payload.get("confirmedIds") or []) if x is not None}
    deleted = {str(x) for x in (payload.get("deletedIds") or []) if x is not None}

    def _op_id(o: dict[str, Any]) -> str:
        return str(o.get("id") or "")

    imported = [
        o
        for o in imported_raw
        if _op_id(o) and _op_id(o) not in confirmed and _op_id(o) not in deleted
    ]
    review_count = len(imported)
    drawer_ops = [
        o
        for o in (payload.get("drawerOps") or [])
        if isinstance(o, dict) and str(o.get("status") or "open") == "open"
    ]
    drawer_count = len(drawer_ops)

    month_budget_pct = round((total_fact / total_plan) * 100) if total_plan > 0 else None
    expected_pct = round((ctx["dayN"] / max(ctx["days"], 1)) * 100) if ctx["dayN"] else 0
    tempo_delta_pp = (month_budget_pct - expected_pct) if month_budget_pct is not None else None
    plan_vs_fact_rub = round(total_fact - total_plan * (day_n / days) if days and total_plan else total_fact - (total_plan if total_plan else 0))
    # vs calendar-expected plan spend for the brief: expected rub = total_plan * day_n/days
    expected_fact_rub = round(total_plan * (day_n / days)) if total_plan and days else 0
    delta_vs_expected_rub = round(total_fact - expected_fact_rub)
    delta_vs_expected_pct = tempo_delta_pp

    balance = round(buffer - overspend)
    flags: list[str] = []
    if days_since_expense is not None and days_since_expense >= 5:
        flags.append(f"no_personal_expense_{days_since_expense}d")
    if review_count:
        flags.append(f"review_queue_{review_count}")
    if balance < 0:
        flags.append("balance_negative")
    if tempo_delta_pp is not None and tempo_delta_pp >= 8:
        flags.append("tempo_ahead")
    if free_after_plan < 0:
        flags.append("free_shortfall")
    if drawer_count:
        flags.append(f"drawer_{drawer_count}")

    return {
        "as_of": t.isoformat(),
        "as_of_display": f"{t.day:02d}.{t.month:02d}.{t.year:04d}",
        "month": ym,
        "day_n": ctx["dayN"],
        "days_in_month": ctx["days"],
        "total_plan": round(total_plan),
        "total_fact_to_date": round(total_fact),
        "month_budget_spent_pct": month_budget_pct,
        "expected_spent_pct_by_calendar": expected_pct,
        "expected_fact_rub": expected_fact_rub,
        "delta_vs_expected_rub": delta_vs_expected_rub,
        "delta_vs_expected_pct": delta_vs_expected_pct,
        "tempo_delta_pp": tempo_delta_pp,
        "balance": balance,
        "balance_meaning": "buffer(soft leftovers) − all overspend; rigid leftovers not free cash",
        "kassa": kassa,
        "cash_accounts": cash_accounts,
        "cash_reserves": cash_reserves,
        "reserved": reserved,
        "free_money": free_money,
        "remaining_planned_expenses": remaining,
        "free_after_plan": free_after_plan,
        "watched": watched_unique[:12],
        "overspend_alerts": overspend_alerts[:8],
        "hot_categories": overspend_alerts[:5],
        "categories": groups,
        "expense_rows": exp_rows,
        "gaps": {
            "days_since_last_personal_expense": days_since_expense,
            "review_queue_ops": review_count,
            "drawer_ops": drawer_count,
            "last_personal_expense_date": last_personal.isoformat() if last_personal else None,
        },
        "drawer_count": drawer_count,
        "flags": flags,
        "user_display_name": "Макс",
    }
