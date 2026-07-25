"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import groups, upload
from app.routers.catalog import (
    budgets_router,
    categories_router,
    reports_router,
    rules_router,
    transactions_router,
    wallets_router,
)
from app.seed import seed_defaults


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables (Alembic is the source of truth for migrations;
    # create_all keeps first-run / Replit zero-config friendly).
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        "Разделение личных и бизнес-расходов по одной карте. "
        "Группировка только по магазину, разметка только с подтверждением."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(groups.router)
app.include_router(categories_router)
app.include_router(wallets_router)
app.include_router(rules_router)
app.include_router(transactions_router)
app.include_router(budgets_router)
app.include_router(reports_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}
