from typing import List
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # CORS
    ALLOWED_ORIGINS_STR: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,https://vayu-geop.vercel.app"
    )

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 20

    # GCS
    GCP_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""

    # GEE service account (for Render deployment)
    GOOGLE_APPLICATION_CREDENTIALS_JSON: str = ""

    # Groq
    GROQ_API_KEY: str = ""

    # ACLED conflict data (free — register at acleddata.com, uses OAuth)
    ACLED_EMAIL: str = ""
    ACLED_PASSWORD: str = ""

    # AISStream.io maritime vessel tracking (free — register at aisstream.io)
    AISSTREAM_API_KEY: str = ""

    # Job TTL
    JOB_TTL_SECONDS: int = 3600

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS_STR.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
