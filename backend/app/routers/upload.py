"""Upload CSV statement endpoint."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import UploadResult
from app.services.csv_parser import CsvParseError, parse_statement
from app.services.grouping import import_rows

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("", response_model=UploadResult)
async def upload_csv(
    file: UploadFile = File(..., description="CSV-выписка Т-Банка"),
    db: Session = Depends(get_db),
) -> UploadResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не выбран")

    lower = file.filename.lower()
    if not (lower.endswith(".csv") or lower.endswith(".txt")):
        raise HTTPException(
            status_code=400,
            detail="Ожидается файл .csv (колонки: Дата, Магазин, Сумма, Комментарий)",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Файл пустой")

    try:
        rows = parse_statement(raw)
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = import_rows(db, rows)
    return UploadResult(
        imported=result["imported"],
        skipped_duplicates=result["skipped_duplicates"],
        groups_created=result["groups_created"],
        groups_updated=result["groups_updated"],
        total_operations=result["total_operations"],
        pending_groups=result["pending_groups"],
        review_groups=result["review_groups"],
    )
