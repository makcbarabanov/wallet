"""Categories, wallets, rules, transactions, budgets, reports."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Budget, Category, Rule, Transaction, Wallet
from app.schemas import (
    BudgetCreate,
    BudgetOut,
    CategoryCreate,
    CategoryOut,
    PlanFactRow,
    ReportSummary,
    RuleOut,
    StatsOut,
    TransactionLabel,
    TransactionOut,
    WalletOut,
)
from app.services.grouping import label_transaction
from app.services.reports import build_report, get_stats, plan_fact

categories_router = APIRouter(prefix="/api/categories", tags=["categories"])
wallets_router = APIRouter(prefix="/api/wallets", tags=["wallets"])
rules_router = APIRouter(prefix="/api/rules", tags=["rules"])
transactions_router = APIRouter(prefix="/api/transactions", tags=["transactions"])
budgets_router = APIRouter(prefix="/api/budgets", tags=["budgets"])
reports_router = APIRouter(prefix="/api/reports", tags=["reports"])


# ─── Categories ───────────────────────────────────────────────────────────────


@categories_router.get("", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryOut]:
    cats = db.query(Category).order_by(Category.name).all()
    return [CategoryOut.model_validate(c) for c in cats]


@categories_router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    body: CategoryCreate, db: Session = Depends(get_db)
) -> CategoryOut:
    existing = db.query(Category).filter(Category.name == body.name.strip()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Категория уже существует")
    cat = Category(
        name=body.name.strip(),
        color=body.color,
        wallet_id=body.wallet_id,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return CategoryOut.model_validate(cat)


@categories_router.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, db: Session = Depends(get_db)) -> None:
    cat = db.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    if cat.is_system:
        raise HTTPException(status_code=400, detail="Системную категорию нельзя удалить")
    in_use = (
        db.query(Transaction).filter(Transaction.category_id == category_id).first()
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Категория используется в операциях — сначала переразметьте их",
        )
    db.delete(cat)
    db.commit()


# ─── Wallets ──────────────────────────────────────────────────────────────────


@wallets_router.get("", response_model=list[WalletOut])
def list_wallets(db: Session = Depends(get_db)) -> list[WalletOut]:
    wallets = db.query(Wallet).order_by(Wallet.id).all()
    return [WalletOut.model_validate(w) for w in wallets]


# ─── Rules ────────────────────────────────────────────────────────────────────


@rules_router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)) -> list[RuleOut]:
    rules = (
        db.query(Rule)
        .options(joinedload(Rule.category), joinedload(Rule.wallet))
        .order_by(Rule.hit_count.desc())
        .all()
    )
    out: list[RuleOut] = []
    for r in rules:
        item = RuleOut.model_validate(r)
        item.category_name = r.category.name if r.category else None
        item.wallet_name = r.wallet.name if r.wallet else None
        out.append(item)
    return out


@rules_router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> None:
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    db.delete(rule)
    db.commit()


# ─── Transactions ─────────────────────────────────────────────────────────────


@transactions_router.get("", response_model=list[TransactionOut])
def list_transactions(
    status: Optional[str] = None,
    wallet_id: Optional[int] = None,
    category_id: Optional[int] = None,
    store: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    query = db.query(Transaction).options(
        joinedload(Transaction.category),
        joinedload(Transaction.wallet),
    )
    if status:
        query = query.filter(Transaction.status == status)
    if wallet_id is not None:
        query = query.filter(Transaction.wallet_id == wallet_id)
    if category_id is not None:
        query = query.filter(Transaction.category_id == category_id)
    if store:
        query = query.filter(Transaction.store.ilike(f"%{store}%"))

    txs = (
        query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        TransactionOut(
            id=t.id,
            date=t.date,
            store=t.store,
            amount=t.amount,
            comment=t.comment,
            status=t.status,
            group_id=t.group_id,
            category_id=t.category_id,
            wallet_id=t.wallet_id,
            category_name=t.category.name if t.category else None,
            wallet_name=t.wallet.name if t.wallet else None,
        )
        for t in txs
    ]


@transactions_router.post("/{transaction_id}/label", response_model=TransactionOut)
def apply_transaction_label(
    transaction_id: int,
    body: TransactionLabel,
    db: Session = Depends(get_db),
) -> TransactionOut:
    """Secondary mode: label a single operation as an exception."""
    try:
        return label_transaction(
            db,
            transaction_id=transaction_id,
            category_id=body.category_id,
            wallet_id=body.wallet_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ─── Budgets ──────────────────────────────────────────────────────────────────


@budgets_router.get("", response_model=list[BudgetOut])
def list_budgets(
    period: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[BudgetOut]:
    query = db.query(Budget).options(
        joinedload(Budget.category), joinedload(Budget.wallet)
    )
    if period:
        query = query.filter(Budget.period == period)
    budgets = query.order_by(Budget.period.desc()).all()
    return [
        BudgetOut(
            id=b.id,
            category_id=b.category_id,
            wallet_id=b.wallet_id,
            period=b.period,
            planned_amount=b.planned_amount,
            category_name=b.category.name if b.category else None,
            wallet_name=b.wallet.name if b.wallet else None,
        )
        for b in budgets
    ]


@budgets_router.post("", response_model=BudgetOut, status_code=201)
def create_budget(body: BudgetCreate, db: Session = Depends(get_db)) -> BudgetOut:
    if db.get(Category, body.category_id) is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    if body.wallet_id is not None and db.get(Wallet, body.wallet_id) is None:
        raise HTTPException(status_code=404, detail="Кошелёк не найден")

    existing = (
        db.query(Budget)
        .filter(
            Budget.category_id == body.category_id,
            Budget.wallet_id == body.wallet_id,
            Budget.period == body.period,
        )
        .first()
    )
    if existing:
        existing.planned_amount = body.planned_amount
        db.commit()
        db.refresh(existing)
        budget = existing
    else:
        budget = Budget(
            category_id=body.category_id,
            wallet_id=body.wallet_id,
            period=body.period,
            planned_amount=body.planned_amount,
        )
        db.add(budget)
        db.commit()
        db.refresh(budget)

    budget = (
        db.query(Budget)
        .options(joinedload(Budget.category), joinedload(Budget.wallet))
        .filter(Budget.id == budget.id)
        .first()
    )
    return BudgetOut(
        id=budget.id,
        category_id=budget.category_id,
        wallet_id=budget.wallet_id,
        period=budget.period,
        planned_amount=budget.planned_amount,
        category_name=budget.category.name if budget.category else None,
        wallet_name=budget.wallet.name if budget.wallet else None,
    )


@budgets_router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, db: Session = Depends(get_db)) -> None:
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Бюджет не найден")
    db.delete(budget)
    db.commit()


# ─── Reports ──────────────────────────────────────────────────────────────────


@reports_router.get("/summary", response_model=ReportSummary)
def report_summary(
    wallet_id: Optional[int] = None,
    period_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    period_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    labeled_only: bool = False,
    db: Session = Depends(get_db),
) -> ReportSummary:
    return build_report(
        db,
        wallet_id=wallet_id,
        period_from=period_from,
        period_to=period_to,
        labeled_only=labeled_only,
    )


@reports_router.get("/plan-fact", response_model=list[PlanFactRow])
def report_plan_fact(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    wallet_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> list[PlanFactRow]:
    return plan_fact(db, period=period, wallet_id=wallet_id)


@reports_router.get("/stats", response_model=StatsOut)
def report_stats(db: Session = Depends(get_db)) -> StatsOut:
    return get_stats(db)
