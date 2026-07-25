"""Pydantic request/response schemas."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─── Wallets ─────────────────────────────────────────────────────────────────


class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    kind: str


# ─── Categories ───────────────────────────────────────────────────────────────


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    color: str = Field(default="#3B82F6", pattern=r"^#[0-9A-Fa-f]{6}$")
    wallet_id: Optional[int] = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    wallet_id: Optional[int] = None
    is_system: bool = False


# ─── Transactions ─────────────────────────────────────────────────────────────


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    store: str
    amount: float
    comment: str
    status: str
    group_id: Optional[int] = None
    category_id: Optional[int] = None
    wallet_id: Optional[int] = None
    category_name: Optional[str] = None
    wallet_name: Optional[str] = None


class TransactionLabel(BaseModel):
    """Single-transaction exception labeling (secondary mode)."""

    category_id: int
    wallet_id: int


# ─── Groups ───────────────────────────────────────────────────────────────────


class GroupOut(BaseModel):
    """API shape required by the product brief."""

    groupName: str
    operations: list[TransactionOut]
    count: int
    totalSum: float
    suggestedCategory: Optional[CategoryOut] = None
    suggestedWallet: Optional[WalletOut] = None
    status: str  # pending | review | labeled
    id: int
    category: Optional[CategoryOut] = None
    wallet: Optional[WalletOut] = None


class GroupLabelRequest(BaseModel):
    """Apply category + wallet to an entire merchant group (primary mode)."""

    category_id: int
    wallet_id: int
    # Persist as a local suggestion rule for future imports
    save_rule: bool = True


class GroupLabelResponse(BaseModel):
    group: GroupOut
    labeled_count: int


# ─── Upload ───────────────────────────────────────────────────────────────────


class UploadResult(BaseModel):
    imported: int
    skipped_duplicates: int
    groups_created: int
    groups_updated: int
    total_operations: int
    pending_groups: int
    review_groups: int


# ─── Rules ────────────────────────────────────────────────────────────────────


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pattern: str
    match_type: str
    category_id: int
    wallet_id: int
    hit_count: int
    category_name: Optional[str] = None
    wallet_name: Optional[str] = None


# ─── Budgets / Plan-fact ──────────────────────────────────────────────────────


class BudgetCreate(BaseModel):
    category_id: int
    wallet_id: Optional[int] = None
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    planned_amount: float = Field(..., gt=0)


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    wallet_id: Optional[int] = None
    period: str
    planned_amount: float
    category_name: Optional[str] = None
    wallet_name: Optional[str] = None


class PlanFactRow(BaseModel):
    category_id: int
    category_name: str
    category_color: str
    wallet_id: Optional[int] = None
    wallet_name: Optional[str] = None
    period: str
    planned: float
    actual: float
    variance: float
    pct_used: Optional[float] = None


# ─── Reports ──────────────────────────────────────────────────────────────────


class CategorySlice(BaseModel):
    category_id: Optional[int]
    category_name: str
    color: str
    total: float
    count: int
    wallet_id: Optional[int] = None
    wallet_name: Optional[str] = None


class MonthBar(BaseModel):
    period: str  # YYYY-MM
    personal: float
    business: float
    total: float


class WalletSummary(BaseModel):
    wallet_id: int
    wallet_name: str
    kind: str
    total: float
    count: int


class ReportSummary(BaseModel):
    total_operations: int
    labeled_operations: int
    pending_operations: int
    review_operations: int
    total_amount: float
    personal_amount: float
    business_amount: float
    unlabeled_amount: float
    by_category: list[CategorySlice]
    by_month: list[MonthBar]
    by_wallet: list[WalletSummary]


class StatsOut(BaseModel):
    total_operations: int
    pending_groups: int
    review_groups: int
    labeled_groups: int
    categories_count: int
    rules_count: int
