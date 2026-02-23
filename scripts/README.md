# DynamoDB Table Provisioning Script

Provisions all DynamoDB tables required by Cortex services (`cortex-application` and `cortex-ingestion`).

## Prerequisites

- Python 3.10+
- `boto3` (`pip install boto3`)
- AWS credentials configured (`aws configure` or environment variables)

## Usage

```bash
# Dry run — see what would be created
python provision-dynamodb-tables.py --region us-east-1 --env prod --dry-run

# Actually create tables
python provision-dynamodb-tables.py --region us-east-1 --env prod
```

### Arguments

| Flag | Required | Description |
|------|----------|-------------|
| `--region` | Yes | AWS region (e.g. `us-east-1`) |
| `--env` | Yes | Environment: `local`, `staging`, or `prod` |
| `--dry-run` | No | Print what would be created without making changes |

## Tables Provisioned

| Table | PK | SK | GSIs | Used By |
|-------|----|----|------|---------|
| `cortex-users` | `pk` (S) | `sk` (S) | GSI1: `GSI1PK`/`GSI1SK` | NextAuth user records |
| `cortex_user_api_keys_{env}` | `api_key_id` (S) | `user_email` (S) | — | API key auth |
| `tenant-id-mapping-{env}` | `Organisation` (S) | `Organisation_tenant_id` (S) | — | Multi-tenant routing |
| `user_indexed_data_status` | `composite_pk` (S) | — | — | Ingestion status tracking |
| `user_metadata_{env}` | `user_id` (S) | — | — | User metadata (kafka topics, etc.) |
| `user_details_{env}` | `email` (S) | — | — | User profile details |
| `users_to_sign_up_{env}` | `email` (S) | — | — | Onboarding tracking |
| `fetch_data_schedule` | `uid` (S) | `app_name` (S) | — | Legacy data fetch scheduling |
| `user` | `uid` (S) | — | — | Legacy user table |
| `token_bucket_rate_limiter` | `pk` (S) | — | — | Rate limiting (TTL on `expires_at`) |

All tables use `PAY_PER_REQUEST` billing mode.

## Idempotency

The script is idempotent — it skips tables that already exist.
