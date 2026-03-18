from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Pulumi S3 backend
    pulumi_backend_url: str = "s3://my-pulumi-state-bucket"
    pulumi_secrets_provider: str = "awskms://alias/pulumi-secrets"
    pulumi_work_dir: str = "."

    # AWS credentials
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "byoc_platform"

    # Redis (Celery broker)
    redis_url: str = "redis://redis:6379/0"

    # Config storage — kept for backward compat but now backed by MongoDB
    config_storage_path: str = "config"

    # GitHub (for GitOps writer)
    github_pat: str = ""
    github_repo: str = "opengig/cortex"

    # Auth / JWT
    jwt_secret: str = "change-me-in-production"
    jwt_expires_in_hours: int = 168  # 7 days

    # CORS
    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
