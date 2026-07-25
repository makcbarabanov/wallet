"""Merchant groups — list, label (bulk), move to review."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import GroupLabelRequest, GroupLabelResponse, GroupOut
from app.services.grouping import label_group, list_groups
from app.models import MerchantGroup, Transaction
from datetime import datetime

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
def get_groups(
    status: Optional[str] = Query(
        None, description="pending | review | labeled"
    ),
    q: Optional[str] = Query(None, description="Поиск по названию магазина"),
    db: Session = Depends(get_db),
) -> list[GroupOut]:
    if status and status not in {"pending", "review", "labeled"}:
        raise HTTPException(status_code=400, detail="Некорректный status")
    return list_groups(db, status=status, q=q)


@router.get("/{group_id}", response_model=GroupOut)
def get_group(group_id: int, db: Session = Depends(get_db)) -> GroupOut:
    groups = list_groups(db)
    for g in groups:
        if g.id == group_id:
            return g
    raise HTTPException(status_code=404, detail="Группа не найдена")


@router.post("/{group_id}/label", response_model=GroupLabelResponse)
def apply_group_label(
    group_id: int,
    body: GroupLabelRequest,
    db: Session = Depends(get_db),
) -> GroupLabelResponse:
    """
    Primary labeling mode: apply category + wallet to every operation in the group.
    Never auto-applied — requires an explicit API call from the UI.
    """
    try:
        group, count = label_group(
            db,
            group_id=group_id,
            category_id=body.category_id,
            wallet_id=body.wallet_id,
            save_rule=body.save_rule,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return GroupLabelResponse(group=group, labeled_count=count)


@router.post("/{group_id}/review", response_model=GroupOut)
def send_to_review(group_id: int, db: Session = Depends(get_db)) -> GroupOut:
    """Park a pending group in «На согласование»."""
    group = db.get(MerchantGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if group.status == "labeled":
        raise HTTPException(
            status_code=400,
            detail="Размеченную группу нельзя отправить на согласование",
        )

    group.status = "review"
    group.updated_at = datetime.utcnow()
    for tx in db.query(Transaction).filter(Transaction.group_id == group_id):
        if tx.category_id is None:
            tx.status = "review"
    db.commit()

    result = list_groups(db)
    for g in result:
        if g.id == group_id:
            return g
    raise HTTPException(status_code=404, detail="Группа не найдена")
