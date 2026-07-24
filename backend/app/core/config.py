from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    ENV: str = "development"
    DEBUG: bool = False
    ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    ANTHROPIC_API_KEY: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_TASK_ALWAYS_EAGER: bool = False
    CELERY_BROKER_VISIBILITY_TIMEOUT: int = 3600

    APPLICATION_VERSION: str = "1.0.0"

    SUPABASE_STORAGE_BUCKET: str = "design-files"
    LOCAL_STORAGE_ROOT: str = ""

    MAX_BATCH_VARIANTS: int = 25
    MAX_CONCURRENT_JOBS_PER_USER: int = 3
    MAX_CONCURRENT_SIMULATION_JOBS_PER_USER: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
