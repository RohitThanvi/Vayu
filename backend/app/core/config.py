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

    # Executive summary emails (services/reporting/) — RESEND_API_KEY: free
    # signup at resend.com, no card; sending to arbitrary recipients needs
    # a verified sending domain there (not just an API key) or every send
    # will fail outside Resend's own sandbox test address.
    RESEND_API_KEY: str = ""
    REPORT_FROM_EMAIL: str = "reports@vayu.dev"   # must match a domain verified in Resend
    # SMTP fallback — for when you don't own a domain to verify with Resend
    # (Resend, and every domain-based transactional API, requires DNS
    # records on a domain you control; without one, Resend can only send
    # to your own signup address). Gmail's own SMTP relay needs no
    # domain at all — an app password on any Gmail account works. See
    # reporting/email_sender.py for setup. Empty by default; only used
    # when RESEND_API_KEY isn't set.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""       # e.g. yourname@gmail.com
    SMTP_PASSWORD: str = ""   # a 16-char Gmail APP PASSWORD, not your normal login password
    # Where the landing page's Contact form is delivered. Falls back to
    # SMTP_USER (mailing yourself) if unset, since there's no other safe
    # default destination without a real domain/support inbox yet.
    ADMIN_EMAIL: str = ""
    # Shared secret for the "view contact messages" admin endpoints
    # (X-Admin-Key header) — separate from ADMIN_EMAIL since that's just
    # a mailbox, not a credential. Unset by default, which disables the
    # endpoints entirely (see auth_endpoints.py) rather than leaving them
    # open with a blank/guessable key.
    ADMIN_API_KEY: str = ""
    # Postgres connection string (Supabase) for the auth (and, going
    # forward, other) tables — see services/auth/db.py. The contact-form
    # messages table deliberately stays on local SQLite (messages.sqlite3),
    # per explicit decision — it's lower-stakes, append-only data, no
    # need to add it to the migration.
    DATABASE_URL: str = ""
    FRONTEND_URL: str = "https://vayu-geop.vercel.app"
    # "HH:MM" 24h, interpreted in REPORT_TIMEZONE — see reporting/scheduler.py
    DAILY_REPORT_TIME: str = "12:00"
    REPORT_TIMEZONE: str = "Asia/Kolkata"

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
    # OpenSky aircraft tracking — NOTE: these are no longer used by the main
    # backend directly. OpenSky access now goes through the same bridge
    # service AIS uses (poll AIS_BRIDGE_URL + "/aircraft" below), since
    # OpenSky ConnectTimeouts from Render's IP range the same way AISStream
    # did. Set OPENSKY_CLIENT_ID/SECRET on the BRIDGE deployment instead —
    # left here only so an already-set Render env var doesn't error at
    # startup; harmless if unused.
    OPENSKY_CLIENT_ID: str = ""
    OPENSKY_CLIENT_SECRET: str = ""

    # CPCB real-time Air Quality Index (India) — free, register at
    # data.gov.in (email signup, no card) and get a key from your account's
    # "My Account" -> API keys. See services/intel/air_quality.py.
    AQI_API_KEY: str = ""

    # Commodity price ticker — no config needed here anymore. Originally
    # used Alpha Vantage (needed a key, 25/day cap proved unworkable
    # against Render's free-tier cold-start pattern), replaced with Yahoo
    # Finance's unofficial keyless chart API — see
    # services/intel/commodity_prices.py. ALPHAVANTAGE_API_KEY left here,
    # unused, only so an already-set Render env var doesn't error at
    # startup — safe to remove from Render whenever convenient.
    ALPHAVANTAGE_API_KEY: str = ""

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
