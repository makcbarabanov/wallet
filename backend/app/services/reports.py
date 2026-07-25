"""Report aggregation: tables, category pie, monthly bars, plan-fact."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Budget, Category, Transaction, Wallet
from app.schemas import (
    CategorySlice,
    MonthBar,
    PlanFactRow,
    ReportSummary,
    StatsOut,
    WalletSummary,
)
from app.models import MerchantGroup, Rule


def get_stats(db: Session) -> StatsOut:
    from sqlalchemy import func

    return StatsOut(
        total_operations=db.query(func.count(Transaction.id)).scalar() or 0,
        pending_groups=db.query(func.count(MerchantGroup.id))
        .filter(MerchantGroup.status == "pending")
        .scalar()
        or 0,
        review_groups=db.query(func.count(MerchantGroup.id))
        .filter(MerchantGroup.status == "review")
        .scalar()
        or 0,
        labeled_groups=db.query(func.count(MerchantGroup.id))
        .filter(MerchantGroup.status == "labeled")
        .scalar()
        or 0,
        categories_count=db.query(func.count(Category.id)).scalar() or 0,
        rules_count=db.query(func.count(Rule.id)).scalar() or 0,
    )


def build_report(
    db: Session,
    *,
    wallet_id: Optional[int] = None,
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    labeled_only: bool = False,
) -> ReportSummary:
    """
    Aggregate labeled (and optionally all) transactions into report slices.

    Amounts: expenses are typically negative in bank CSVs; we keep the sign
    so totals reflect real cash flow. Charts on the frontend use abs() for display.
    """
    query = db.query(Transaction).options(
        joinedload(Transaction.category),
        joinedload(Transaction.wallet),
    )
    if wallet_id is not None:
        query = query.filter(Transaction.wallet_id == wallet_id)
    if labeled_only:
        query = query.filter(Transaction.status == "labeled")
    if period_from:
        query = query.filter(Transaction.date >= period_from)
    if period_to:
        query = query.filter(Transaction.date <= period_to)

    txs = query.all()

    total_ops = len(txs)
    labeled_ops = sum(1 for t in txs if t.status == "labeled")
    pending_ops = sum(1 for t in txs if t.status == "pending")
    review_ops = sum(1 for t in txs if t.status == "review")
    total_amount = sum(t.amount for t in txs)

    personal = 0.0
    business = 0.0
    unlabeled = 0.0
    wallet_totals: dict[int, dict] = defaultdict(
        lambda: {"total": 0.0, "count": 0, "name": "", "kind": ""}
    )
    cat_totals: dict[tuple, dict] = defaultdict(
        lambda: {"total": 0.0, "count": 0, "name": "Без категории", "color": "#94A3B8", "wallet_id": None, "wallet_name": None}
    )
    month_totals: dict[str, dict] = defaultdict(
        lambda: {"personal": 0.0, "business": 0.0, "total": 0.0}
    )

    wallets_by_id = {w.id: w for w in db.query(Wallet).all()}

    for tx in txs:
        period = tx.date.strftime("%Y-%m")
        month_totals[period]["total"] += tx.amount

        if tx.wallet_id and tx.wallet_id in wallets_by_id:
            w = wallets_by_id[tx.wallet_id]
            wallet_totals[w.id]["total"] += tx.amount
            wallet_totals[w.id]["count"] += 1
            wallet_totals[w.id]["name"] = w.name
            wallet_totals[w.id]["kind"] = w.kind
            if w.kind == "personal":
                personal += tx.amount
                month_totals[period]["personal"] += tx.amount
            elif w.kind == "business":
                business += tx.amount
                month_totals[period]["business"] += tx.amount
        else:
            unlabeled += tx.amount

        key = (tx.category_id, tx.wallet_id)
        bucket = cat_totals[key]
        bucket["total"] += tx.amount
        bucket["count"] += 1
        if tx.category:
            bucket["name"] = tx.category.name
            bucket["color"] = tx.category.color
        if tx.wallet:
            bucket["wallet_id"] = tx.wallet.id
            bucket["wallet_name"] = tx.wallet.name

    by_category = [
        CategorySlice(
            category_id=key[0],
            category_name=val["name"],
            color=val["color"],
            total=round(val["total"], 2),
            count=val["count"],
            wallet_id=val["wallet_id"],
            wallet_name=val["wallet_name"],
        )
        for key, val in cat_totals.items()
    ]
    by_category.sort(key=lambda s: abs(s.total), reverse=True)

    by_month = [
        MonthBar(
            period=period,
            personal=round(vals["personal"], 2),
            business=round(vals["business"], 2),
            total=round(vals["total"], 2),
        )
        for period, vals in sorted(month_totals.items())
    ]

    by_wallet = [
        WalletSummary(
            wallet_id=wid,
            wallet_name=vals["name"],
            kind=vals["kind"],
            total=round(vals["total"], 2),
            count=vals["count"],
        )
        for wid, vals in wallet_totals.items()
    ]

    return ReportSummary(
        total_operations=total_ops,
        labeled_operations=labeled_ops,
        pending_operations=pending_ops,
        review_operations=review_ops,
        total_amount=round(total_amount, 2),
        personal_amount=round(personal, 2),
        business_amount=round(business, 2),
        unlabeled_amount=round(unlabeled, 2),
        by_category=by_category,
        by_month=by_month,
        by_wallet=by_wallet,
    )


def plan_fact(
    db: Session,
    *,
    period: str,
    wallet_id: Optional[int] = None,
) -> list[PlanFactRow]:
    """Compare budgets for a YYYY-MM period against labeled actuals."""
    budgets_q = db.query(Budget).options(
        joinedload(Budget.category),
        joinedload(Budget.wallet),
    ).filter(Budget.period == period)
    if wallet_id is not None:
        budgets_q = budgets_q.filter(Budget.wallet_id == wallet_id)
    budgets = budgets_q.all()

    # Actual spend per (category, wallet) for the period
    txs = (
        db.query(Transaction)
        .filter(Transaction.status == "labeled")
        .filter(Transaction.date >= f"{period}-01")
        .filter(Transaction.date < _next_month(period))
        .all()
    )
    actuals: dict[tuple, float] = defaultdict(float)
    for tx in txs:
        actuals[(tx.category_id, tx.wallet_id)] += tx.amount

    rows: list[PlanFactRow] = []
    for b in budgets:
        # Use abs for «spent» comparison when amounts are negative expenses
        actual_raw = actuals.get((b.category_id, b.wallet_id), 0.0)
        actual_spent = abs(actual_raw) if actual_raw < 0 else actual_raw
        planned = b.planned_amount
        variance = planned - actual_spent
        pct = round(actual_spent / planned * 100, 1) if planned else None
        rows.append(
            PlanFactRow(
                category_id=b.category_id,
                category_name=b.category.name if b.category else "?",
                category_color=b.category.color if b.category else "#94A3B8",
                wallet_id=b.wallet_id,
                wallet_name=b.wallet.name if b.wallet else None,
                period=period,
                planned=planned,
                actual=round(actual_spent, 2),
                variance=round(variance, 2),
                pct_used=pct,
            )
        )

    rows.sort(key=lambda r: r.pct_used or 0, reverse=True)
    return rows


def _next_month(period: str) -> str:
    year, month = map(int, period.split("-"))
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"
