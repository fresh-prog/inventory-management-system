from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/restaurant"
    jwt_secret: str = "dev-only-change-me"
    access_token_minutes: int = 720  # one restaurant shift
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    frontend_dist: str | None = None  # serve the built SPA from here when set
    cors_origins: list[str] = ["http://localhost:5173"]
    deployment_profile: str = "branch"  # branch | cloud (cloud sync arrives in a later stage)
    cloud_sync_url: str | None = None

    def validate_for_production(self) -> None:
        if self.env == "production" and (
            self.jwt_secret == "dev-only-change-me" or len(self.jwt_secret) < 32
        ):
            raise RuntimeError("APP_JWT_SECRET must be set to a random value of 32+ characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
