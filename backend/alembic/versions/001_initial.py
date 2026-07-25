"""Initial schema: wallets, categories, groups, transactions, rules, budgets.

Revision ID: 001_initial
Revises:
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("wallet_id", sa.Integer(), sa.ForeignKey("wallets.id"), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "suggested_category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id"),
            nullable=True,
        ),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True
        ),
        sa.Column(
            "wallet_id", sa.Integer(), sa.ForeignKey("wallets.id"), nullable=True
        ),
        sa.Column("labeled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("store", sa.String(512), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True
        ),
        sa.Column(
            "wallet_id", sa.Integer(), sa.ForeignKey("wallets.id"), nullable=True
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_transactions_store", "transactions", ["store"])
    op.create_index("ix_transactions_group_id", "transactions", ["group_id"])

    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pattern", sa.String(512), nullable=False),
        sa.Column("match_type", sa.String(16), nullable=False, server_default="exact"),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False
        ),
        sa.Column(
            "wallet_id", sa.Integer(), sa.ForeignKey("wallets.id"), nullable=False
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("pattern", "match_type", name="uq_rule_pattern_type"),
    )
    op.create_index("ix_rules_pattern", "rules", ["pattern"])

    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False
        ),
        sa.Column(
            "wallet_id", sa.Integer(), sa.ForeignKey("wallets.id"), nullable=True
        ),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("planned_amount", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "category_id",
            "wallet_id",
            "period",
            name="uq_budget_cat_wallet_period",
        ),
    )


def downgrade() -> None:
    op.drop_table("budgets")
    op.drop_index("ix_rules_pattern", table_name="rules")
    op.drop_table("rules")
    op.drop_index("ix_transactions_group_id", table_name="transactions")
    op.drop_index("ix_transactions_store", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("groups")
    op.drop_table("categories")
    op.drop_table("wallets")
