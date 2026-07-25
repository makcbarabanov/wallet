"""Seed default wallets and categories."""

from sqlalchemy.orm import Session

from app.models import Category, Wallet

DEFAULT_WALLETS = [
    {"name": "Личный", "slug": "personal", "kind": "personal"},
    {"name": "Бизнес", "slug": "business", "kind": "business"},
]

DEFAULT_CATEGORIES = [
    {"name": "Продукты", "color": "#22C55E"},
    {"name": "Транспорт", "color": "#3B82F6"},
    {"name": "Подписки", "color": "#8B5CF6"},
    {"name": "Связь", "color": "#06B6D4"},
    {"name": "Здоровье", "color": "#EF4444"},
    {"name": "Одежда", "color": "#EC4899"},
    {"name": "Дом и быт", "color": "#84CC16"},
    {"name": "Развлечения", "color": "#F97316"},
    {"name": "Образование", "color": "#6366F1"},
    {"name": "Командировки", "color": "#0EA5E9"},
    {"name": "Офис и техника", "color": "#64748B"},
    {"name": "Реклама и маркетинг", "color": "#D946EF"},
    {"name": "Налоги и взносы", "color": "#78716C"},
    {"name": "Зарплата сотрудникам", "color": "#14B8A6"},
    {"name": "Прочее", "color": "#94A3B8"},
]


def seed_defaults(db: Session) -> None:
    """Idempotently insert default wallets and categories."""
    for w in DEFAULT_WALLETS:
        if not db.query(Wallet).filter(Wallet.slug == w["slug"]).first():
            db.add(Wallet(**w))

    for c in DEFAULT_CATEGORIES:
        if not db.query(Category).filter(Category.name == c["name"]).first():
            db.add(Category(name=c["name"], color=c["color"], is_system=True))

    db.commit()
