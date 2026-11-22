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
    deployment_profile: str = "branch"  # branch | cloud
    files_dir: str = "./data/files"  # local document storage (receipts)
    max_upload_mb: int = 10
    min_free_mb: int = 200  # refuse new documents below this free space (the database needs room)
    # cloud profile only: master key deriving per-branch sync secrets (keep secret, 32+ chars)
    cloud_master_key: str = "dev-only-cloud-master-key-change-me"
    sync_batch_size: int = 100
    sync_max_batch_bytes: int = 2_000_000
    sync_max_backoff_seconds: int = 900
    outbox_retention_days: int = 90
    # abuse protection (per client IP, per process)
    rate_limit_login_per_minute: int = 20
    rate_limit_api_per_minute: int = 1200
    rate_limit_register_per_hour: int = 30  # cloud: registration-code guessing
    app_version: str = "0.4.1"
    metrics_token: str | None = None  # enables GET /api/v1/metrics for Prometheus
    backup_dir: str | None = None  # where scripts/backup.sh writes (for the backup-age metric)

    def validate_for_production(self) -> None:
        if self.env != "production":
            return
        if self.jwt_secret == "dev-only-change-me" or len(self.jwt_secret) < 32:
            raise RuntimeError("APP_JWT_SECRET must be set to a random value of 32+ characters")
        if self.deployment_profile == "cloud" and (
                self.cloud_master_key.startswith("dev-only") or len(self.cloud_master_key) < 32):
            raise RuntimeError("APP_CLOUD_MASTER_KEY must be set to a random value of 32+ characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
