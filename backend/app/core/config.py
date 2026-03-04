from typing import List
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # CORS — comma-separated in env
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://localhost:4173",  # Vite preview
    ]

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 20

    # GCS
    GCP_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""

    # Groq
    GROQ_API_KEY: str = ""

    # Job TTL in seconds (clean up old jobs)
    JOB_TTL_SECONDS: int = 3600  # 1 hour

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
