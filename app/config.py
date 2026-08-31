from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://cfdns:cfdns@localhost/cfdns"
    encryption_key: str = Field(min_length=1)
    sync_interval_minutes: int = Field(default=15, ge=1, le=1440)
    cloudflare_api_base: str = "https://api.cloudflare.com/client/v4"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    admin_password: str = Field(default="cfdns", min_length=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
