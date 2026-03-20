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
    github_branch: str = "main"

    # Auth / JWT
    jwt_secret: str = "change-me-in-production"
    jwt_expires_in_hours: int = 168  # 7 days

    # CORS
    cors_origins: str = "http://localhost:3000"

    # Platform-managed secrets (from .env, not from customer)
    falkordb_password: str = "d6c77M05pV"
    milvus_token: str = "root:Milvus"

    # NextJS platform secrets
    nextjs_nextauth_secret: str = ""
    nextjs_google_client_id: str = ""
    nextjs_google_client_secret: str = ""
    nextjs_auth_dynamodb_id: str = ""
    nextjs_auth_dynamodb_secret: str = ""
    nextjs_aws_config: str = ""
    nextjs_mcp_encryption_key: str = ""
    nextjs_resend_api_key: str = ""
    nextjs_stripe_secret_key: str = ""
    nextjs_frontend_config: str = ""
    nextjs_nextauth_url: str = "http://localhost:3000"
    nextjs_auth_dynamodb_region: str = "us-east-1"
    nextjs_email_from: str = ""

    # AI API keys (platform-managed)
    google_api_key: str = ""
    gemini_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
