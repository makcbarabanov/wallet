"""ORM models — schema for transactions, groups, categories, wallets, rules, budgets."""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Wallet(Base):
    """Personal or business spending pot."""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # personal | business
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="wallet")
    categories: Mapped[list["Category"]] = relationship(back_populates="wallet")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="wallet")


class Category(Base):
    """User-defined spending category (optionally scoped to a wallet)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#3B82F6", nullable=False)
    wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    wallet: Mapped[Optional["Wallet"]] = relationship(back_populates="categories")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")
    rules: Mapped[list["Rule"]] = relationship(back_populates="category")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="category")


class MerchantGroup(Base):
    """
    Aggregation key = store name from the bank statement.
    Never grouped by date or amount — only by «Магазин».
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Exact store name as it appears in CSV
    name: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    # pending | review | labeled
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    suggested_category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=True
    )
    labeled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    suggested_category: Mapped[Optional["Category"]] = relationship(
        foreign_keys=[suggested_category_id]
    )
    category: Mapped[Optional["Category"]] = relationship(foreign_keys=[category_id])
    wallet: Mapped[Optional["Wallet"]] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="group")


class Transaction(Base):
    """Single bank-card operation from a CSV statement."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    store: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # pending | labeled | review
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)

    group_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True, index=True
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=True
    )
    # Dedup hash of date+store+amount+comment
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    group: Mapped[Optional["MerchantGroup"]] = relationship(back_populates="transactions")
    category: Mapped[Optional["Category"]] = relationship(back_populates="transactions")
    wallet: Mapped[Optional["Wallet"]] = relationship(back_populates="transactions")


class Rule(Base):
    """
    Local suggestion rule learned from user labeling decisions.
    No external APIs — suggestions come only from these rules.
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Exact or substring match against store name
    pattern: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    # exact | contains (case-insensitive)
    match_type: Mapped[str] = mapped_column(String(16), default="exact", nullable=False)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=False
    )
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=False
    )
    # How many times this rule was confirmed by the user
    hit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    category: Mapped["Category"] = relationship(back_populates="rules")
    wallet: Mapped["Wallet"] = relationship()

    __table_args__ = (
        UniqueConstraint("pattern", "match_type", name="uq_rule_pattern_type"),
    )


class Budget(Base):
    """Monthly plan amount for a category (+ optional wallet scope)."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=False
    )
    wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=True
    )
    # YYYY-MM
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    planned_amount: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    category: Mapped["Category"] = relationship(back_populates="budgets")
    wallet: Mapped[Optional["Wallet"]] = relationship(back_populates="budgets")

    __table_args__ = (
        UniqueConstraint(
            "category_id", "wallet_id", "period", name="uq_budget_cat_wallet_period"
        ),
    )
