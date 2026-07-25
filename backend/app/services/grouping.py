"""Merchant grouping and labeling services.

Core invariant: groups are keyed ONLY by store name («Магазин»).
Never by date, never by amount.
Categories are NEVER applied without an explicit user confirmation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import Category, MerchantGroup, Transaction, Wallet
from app.schemas import CategoryOut, GroupOut, TransactionOut, WalletOut
from app.services.csv_parser import ParsedRow
from app.services.suggestions import find_suggestion, upsert_rule


def _tx_out(tx: Transaction) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        date=tx.date,
        store=tx.store,
        amount=tx.amount,
        comment=tx.comment,
        status=tx.status,
        group_id=tx.group_id,
        category_id=tx.category_id,
        wallet_id=tx.wallet_id,
        category_name=tx.category.name if tx.category else None,
        wallet_name=tx.wallet.name if tx.wallet else None,
    )


def serialize_group(group: MerchantGroup) -> GroupOut:
    """Build the API group payload required by the product brief."""
    ops = sorted(group.transactions, key=lambda t: (t.date, t.id), reverse=True)
    total = sum(t.amount for t in ops)
    return GroupOut(
        id=group.id,
        groupName=group.name,
        operations=[_tx_out(t) for t in ops],
        count=len(ops),
        totalSum=round(total, 2),
        suggestedCategory=(
            CategoryOut.model_validate(group.suggested_category)
            if group.suggested_category
            else None
        ),
        suggestedWallet=None,
        status=group.status,
        category=CategoryOut.model_validate(group.category) if group.category else None,
        wallet=WalletOut.model_validate(group.wallet) if group.wallet else None,
    )


def _compute_status(count: int, has_suggestion: bool, currently_labeled: bool) -> str:
    """
    pending  — ready for bulk labeling
    review   — 1–2 ops, unknown store (no rule) → «На согласование»
    labeled  — user already confirmed category
    """
    if currently_labeled:
        return "labeled"
    if count <= settings.review_max_count and not has_suggestion:
        return "review"
    return "pending"


def import_rows(db: Session, rows: list[ParsedRow]) -> dict:
    """
    Insert parsed CSV rows, rebuild/update merchant groups, refresh suggestions.

    Deduplicates by fingerprint so re-uploading the same file is safe.
    """
    existing_fps = {
        fp for (fp,) in db.query(Transaction.fingerprint).all()
    }

    imported = 0
    skipped = 0
    new_by_store: dict[str, list[ParsedRow]] = defaultdict(list)

    for row in rows:
        if row.fingerprint in existing_fps:
            skipped += 1
            continue
        new_by_store[row.store].append(row)
        imported += 1

    groups_created = 0
    groups_updated = 0

    # Ensure group rows exist for every store we touch (new + existing)
    all_stores = set(new_by_store.keys())
    # Also refresh groups that already have transactions
    existing_groups = {g.name: g for g in db.query(MerchantGroup).all()}

    for store, store_rows in new_by_store.items():
        group = existing_groups.get(store)
        if group is None:
            group = MerchantGroup(name=store, status="pending")
            db.add(group)
            db.flush()
            existing_groups[store] = group
            groups_created += 1
        else:
            groups_updated += 1

        for row in store_rows:
            db.add(
                Transaction(
                    date=row.date,
                    store=row.store,
                    amount=row.amount,
                    comment=row.comment,
                    fingerprint=row.fingerprint,
                    group_id=group.id,
                    status="pending",
                )
            )

    db.flush()

    # Recompute suggestions + status for ALL groups (cheap for ~1.5k ops)
    refresh_all_groups(db)
    db.flush()  # ensure status updates are visible to subsequent COUNT queries

    total_ops = db.query(func.count(Transaction.id)).scalar() or 0
    pending = (
        db.query(func.count(MerchantGroup.id))
        .filter(MerchantGroup.status == "pending")
        .scalar()
        or 0
    )
    review = (
        db.query(func.count(MerchantGroup.id))
        .filter(MerchantGroup.status == "review")
        .scalar()
        or 0
    )

    db.commit()
    return {
        "imported": imported,
        "skipped_duplicates": skipped,
        "groups_created": groups_created,
        "groups_updated": groups_updated,
        "total_operations": total_ops,
        "pending_groups": pending,
        "review_groups": review,
        "touched_stores": sorted(all_stores),
    }


def refresh_all_groups(db: Session) -> None:
    """Re-evaluate suggested categories and statuses for every merchant group."""
    # Expire identity-map caches so newly inserted transactions are visible
    # (relationship collections may otherwise stay empty after bulk insert).
    db.expire_all()

    counts = dict(
        db.query(Transaction.group_id, func.count(Transaction.id))
        .filter(Transaction.group_id.isnot(None))
        .group_by(Transaction.group_id)
        .all()
    )
    groups = db.query(MerchantGroup).all()

    for group in groups:
        suggestion = find_suggestion(db, group.name)
        count = counts.get(group.id, 0)
        currently_labeled = group.status == "labeled" and group.category_id is not None

        if currently_labeled:
            # Keep labeled state; still refresh suggestion for UI hints
            if suggestion:
                group.suggested_category_id = suggestion.category_id
            continue

        if suggestion:
            group.suggested_category_id = suggestion.category_id
            # Do NOT auto-apply — only suggest
            group.status = _compute_status(
                count, has_suggestion=True, currently_labeled=False
            )
        else:
            group.suggested_category_id = None
            group.status = _compute_status(
                count, has_suggestion=False, currently_labeled=False
            )

        # Sync unlabeled transaction statuses with group status
        db.query(Transaction).filter(
            Transaction.group_id == group.id,
            Transaction.category_id.is_(None),
        ).update({Transaction.status: group.status}, synchronize_session=False)

def list_groups(
    db: Session,
    *,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> list[GroupOut]:
    """Return groups for the labeling UI, richest first."""
    query = (
        db.query(MerchantGroup)
        .options(
            joinedload(MerchantGroup.transactions).joinedload(Transaction.category),
            joinedload(MerchantGroup.transactions).joinedload(Transaction.wallet),
            joinedload(MerchantGroup.suggested_category),
            joinedload(MerchantGroup.category),
            joinedload(MerchantGroup.wallet),
        )
    )
    if status:
        query = query.filter(MerchantGroup.status == status)
    if q:
        query = query.filter(MerchantGroup.name.ilike(f"%{q}%"))

    groups = query.all()
    result: list[GroupOut] = []

    for group in groups:
        out = serialize_group(group)
        # Attach suggested wallet from the matching rule (without applying it)
        suggestion = find_suggestion(db, group.name)
        if suggestion:
            wallet = db.get(Wallet, suggestion.wallet_id)
            if wallet:
                out.suggestedWallet = WalletOut.model_validate(wallet)
        result.append(out)

    # Sort: largest absolute spend first (expenses are often negative)
    result.sort(key=lambda g: abs(g.totalSum), reverse=True)
    return result


def label_group(
    db: Session,
    *,
    group_id: int,
    category_id: int,
    wallet_id: int,
    save_rule: bool = True,
) -> tuple[GroupOut, int]:
    """
    Apply category + wallet to ALL operations in the group.
    This is the primary labeling mode. Never called implicitly.
    """
    group = (
        db.query(MerchantGroup)
        .options(
            joinedload(MerchantGroup.transactions),
            joinedload(MerchantGroup.suggested_category),
            joinedload(MerchantGroup.category),
            joinedload(MerchantGroup.wallet),
        )
        .filter(MerchantGroup.id == group_id)
        .first()
    )
    if group is None:
        raise LookupError(f"Группа {group_id} не найдена")

    category = db.get(Category, category_id)
    wallet = db.get(Wallet, wallet_id)
    if category is None:
        raise LookupError(f"Категория {category_id} не найдена")
    if wallet is None:
        raise LookupError(f"Кошелёк {wallet_id} не найден")

    labeled = 0
    for tx in group.transactions:
        tx.category_id = category_id
        tx.wallet_id = wallet_id
        tx.status = "labeled"
        labeled += 1

    group.category_id = category_id
    group.wallet_id = wallet_id
    group.status = "labeled"
    group.labeled_at = datetime.utcnow()
    group.suggested_category_id = category_id

    if save_rule:
        upsert_rule(
            db,
            store_name=group.name,
            category_id=category_id,
            wallet_id=wallet_id,
            match_type="exact",
        )

    db.commit()
    db.refresh(group)

    # Reload with relationships for serialization
    group = (
        db.query(MerchantGroup)
        .options(
            joinedload(MerchantGroup.transactions).joinedload(Transaction.category),
            joinedload(MerchantGroup.transactions).joinedload(Transaction.wallet),
            joinedload(MerchantGroup.suggested_category),
            joinedload(MerchantGroup.category),
            joinedload(MerchantGroup.wallet),
        )
        .filter(MerchantGroup.id == group_id)
        .first()
    )
    out = serialize_group(group)
    out.suggestedWallet = WalletOut.model_validate(wallet)
    return out, labeled


def label_transaction(
    db: Session,
    *,
    transaction_id: int,
    category_id: int,
    wallet_id: int,
) -> TransactionOut:
    """Exception path: label a single operation differently from its group."""
    tx = (
        db.query(Transaction)
        .options(joinedload(Transaction.category), joinedload(Transaction.wallet))
        .filter(Transaction.id == transaction_id)
        .first()
    )
    if tx is None:
        raise LookupError(f"Операция {transaction_id} не найдена")

    if db.get(Category, category_id) is None:
        raise LookupError(f"Категория {category_id} не найдена")
    if db.get(Wallet, wallet_id) is None:
        raise LookupError(f"Кошелёк {wallet_id} не найден")

    tx.category_id = category_id
    tx.wallet_id = wallet_id
    tx.status = "labeled"
    db.commit()
    db.refresh(tx)
    return _tx_out(tx)

