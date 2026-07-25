"""Suggestion engine based solely on local rules (no external APIs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Rule


@dataclass
class Suggestion:
    category_id: int
    wallet_id: int
    rule_id: int
    match_type: str
    pattern: str


def find_suggestion(db: Session, store_name: str) -> Optional[Suggestion]:
    """
    Find the best local rule matching a store name.

    Priority:
      1. Exact match (case-insensitive)
      2. Contains match with longest pattern, then highest hit_count
    """
    if not store_name:
        return None

    needle = store_name.strip().lower()
    rules: list[Rule] = db.query(Rule).all()

    exact: Optional[Rule] = None
    contains: list[Rule] = []

    for rule in rules:
        pattern = rule.pattern.strip().lower()
        if rule.match_type == "exact":
            if pattern == needle:
                exact = rule
                break
        elif rule.match_type == "contains":
            if pattern and pattern in needle:
                contains.append(rule)

    chosen = exact
    if chosen is None and contains:
        contains.sort(key=lambda r: (len(r.pattern), r.hit_count), reverse=True)
        chosen = contains[0]

    if chosen is None:
        return None

    return Suggestion(
        category_id=chosen.category_id,
        wallet_id=chosen.wallet_id,
        rule_id=chosen.id,
        match_type=chosen.match_type,
        pattern=chosen.pattern,
    )


def upsert_rule(
    db: Session,
    *,
    store_name: str,
    category_id: int,
    wallet_id: int,
    match_type: str = "exact",
) -> Rule:
    """Create or refresh a rule after the user confirms a group label."""
    pattern = store_name.strip()
    existing = (
        db.query(Rule)
        .filter(Rule.pattern == pattern, Rule.match_type == match_type)
        .first()
    )
    if existing:
        existing.category_id = category_id
        existing.wallet_id = wallet_id
        existing.hit_count += 1
        return existing

    rule = Rule(
        pattern=pattern,
        match_type=match_type,
        category_id=category_id,
        wallet_id=wallet_id,
        hit_count=1,
    )
    db.add(rule)
    return rule
