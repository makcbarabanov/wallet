"""Application configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings

# Project root: /wallet
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Runtime settings loaded from environment or defaults."""

    app_name: str = "Wallet"
    database_url: str = f"sqlite:///{DATA_DIR / 'wallet.db'}"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    # Groups with ≤ this many ops and no known rule → «На согласование»
    review_max_count: int = 2

    class Config:
        env_prefix = "WALLET_"


settings = Settings()
