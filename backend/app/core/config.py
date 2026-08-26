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

    # AIS vessel tracking — via our own bridge service (see /ais-bridge),
    # which holds the actual AISStream.io connection and is polled here as
    # plain REST. AISSTREAM_API_KEY itself now lives only on the bridge,
    # not here.
    AIS_BRIDGE_URL: str = ""
    AIS_BRIDGE_API_KEY: str = ""

    # OpenSky aircraft tracking (free — register at opensky-network.org,
    # Account -> API Clients, uses OAuth2 client-credentials. Falls back to
    # anonymous access if unset, which works but is unreliable from a
    # data-center IP — see fetchers.py fetch_opensky for why)
    OPENSKY_CLIENT_ID: str = ""
    OPENSKY_CLIENT_SECRET: str = ""

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
