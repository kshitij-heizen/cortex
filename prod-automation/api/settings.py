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

    # Config storage
    config_storage_path: str = "config"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
