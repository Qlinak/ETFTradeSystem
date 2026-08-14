"""Database configuration for the ETF Trade System."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Environment-driven PostgreSQL settings."""

    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=5433)
    name: str = Field(default="etf_system")
    user: str = Field(default="etf_user")
    password: str = Field(default="etf_password")
    echo_sql: bool = Field(default=False)

    @property
    def sqlalchemy_url(self) -> str:
        return f"postgresql+psycopg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()