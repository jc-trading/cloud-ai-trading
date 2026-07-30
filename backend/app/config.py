"""
Application configuration using Pydantic BaseSettings.
All settings are loaded from environment variables or .env file.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Cloud AI Trading"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "local"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/cloud_ai_trading"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@db:5432/cloud_ai_trading"
    DB_NAME: str = "cloud_ai_trading"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "cloud_ai_trading"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Auth / JWT
    SECRET_KEY: str = "change-this-to-a-secure-random-string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Encryption (for API keys)
    ENCRYPTION_KEY: str = "change-this-to-a-fernet-key"

    # AI Provider Configuration
    AI_PROVIDER: str = "claude"  # Options: "claude", "deepseek", "openai"

    # Claude API
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"  # Changed to cheaper Haiku model

    # OpenAI API
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"  # Very cheap and capable

    # DeepSeek API
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"  # Or "deepseek-coder"

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # Analysis
    ANALYSIS_INTERVAL_MINUTES: int = 3

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Alpaca (system-level keys for market data — separate from user connections)
    ALPACA_API_KEY: str = ""
    ALPACA_API_SECRET: str = ""
    # Which Alpaca environment the system-level keys belong to: "paper" | "live".
    # Sets the default trading endpoint for adapters that aren't given an
    # explicit mode. NOTE: auto-execution ignores this and stays paper-forced
    # (modules/execution/service.py) until the go-live gating is decided.
    ALPACA_MODE: str = "paper"

    @field_validator("ALPACA_MODE")
    @classmethod
    def _validate_alpaca_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("paper", "live"):
            raise ValueError("ALPACA_MODE must be 'paper' or 'live'")
        return v

    @property
    def ALPACA_TRADING_URL(self) -> str:
        # Only the two official endpoints — deliberately NOT free-form via env,
        # so a config edit can never redirect the paper-forced execution path
        # to the live endpoint.
        if self.ALPACA_MODE == "live":
            return "https://api.alpaca.markets"
        return "https://paper-api.alpaca.markets"
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""

    # Finnhub (fundamental analysis data source — earnings, estimates, news, profile)
    FINNHUB_API_KEY: str = ""

    # Exchange defaults
    DEFAULT_SIMULATE_BALANCE: float = 10000.0  # USDT
    BINANCE_FEE_RATE: float = 0.001  # 0.1%
    ALPACA_FEE_RATE: float = 0.0  # commission-free

    # Telegram notifications
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # HALT sentinel: its EXISTENCE refuses all sim entries even when the DB is
    # down. Env-overridable so host-run dev/tests don't need /app (review #11).
    HALT_SENTINEL_PATH: str = "/app/runtime/HALT"

    # System Monitoring
    SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS: int = 60  # Collect metrics every 60 seconds
    SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS: int = 300  # Check task health every 5 minutes
    SYSTEM_LOG_RETENTION_DAYS: int = 30  # Keep logs for 30 days
    SYSTEM_METRICS_RETENTION_DAYS: int = 30  # Keep metrics for 30 days
    SYSTEM_CPU_WARNING_THRESHOLD: float = 80.0  # CPU % threshold for warnings
    SYSTEM_MEMORY_WARNING_THRESHOLD: float = 85.0  # Memory % threshold for warnings
    SYSTEM_DISK_CRITICAL_THRESHOLD: float = 90.0  # Disk % threshold for critical alerts

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Module-level instance for use in non-request contexts (e.g., Celery tasks, notifications)
settings = get_settings()
