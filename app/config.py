from pydantic_settings import BaseSettings

VERSION = "0.9.9.3"


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    DATABASE_URL: str = "postgresql+asyncpg://openlucid:openlucid@localhost:5432/openlucid"
    STORAGE_BASE_PATH: str = "./uploads"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # Auth
    SECRET_KEY: str = "change-me-in-production-use-a-long-random-string"
    JWT_EXPIRE_HOURS: int = 168

    # Email (optional — if neither configured, reset URL is logged instead)
    MAIL_TYPE: str = ""  # "resend" or "smtp"; empty = disabled
    MAIL_FROM: str = "no-reply@openlucid.com"

    # Resend
    RESEND_API_KEY: str = ""

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # Feedback widget — the in-app feedback button is ALWAYS visible.
    # When FEEDBACK_TO_EMAIL is set + a mail provider is configured, submissions
    # are emailed to that address. Otherwise the button falls back to opening
    # FEEDBACK_FALLBACK_URL (a prefilled GitHub Issues URL by default) — so the
    # widget works out of the box for self-hosters without any configuration.
    FEEDBACK_TO_EMAIL: str = ""
    FEEDBACK_FALLBACK_URL: str = "https://github.com/agidesigner/OpenLucid/issues/new"

    APP_URL: str = "http://localhost:8000"

    # CORS
    CORS_ORIGINS: str = "*"

    # Set true to skip auth (e.g. in tests)
    DISABLE_AUTH: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
