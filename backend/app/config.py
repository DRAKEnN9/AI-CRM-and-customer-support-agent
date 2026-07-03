from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Salon AI Agent"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_KEY: str = "your_internal_api_key_here"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:password@db:5432/salon_db"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # WhatsApp Cloud API
    WHATSAPP_TOKEN: str = "your_meta_access_token"
    WHATSAPP_PHONE_NUMBER_ID: str = "your_phone_number_id"
    WHATSAPP_VERIFY_TOKEN: str = "your_webhook_verify_token"
    WHATSAPP_APP_SECRET: str = "your_app_secret" # Used for signature verification

    # AI / LLM (LiteLLM)
    LITEMLLM_API_KEY: str = "your_litellm_key"
    LITEMLLM_BASE_URL: str = "https://api.litellm.ai"
    LLM_MODEL: str = "gemini/gemini-2.5-flash"
    EMBEDDING_MODEL: str = "gemini/gemini-embedding-2"

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # Admin/Handoff
    SALON_ADMIN_PHONE: str = "your_admin_phone_number"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
