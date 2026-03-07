#!/usr/bin/env python3
"""
Standalone DynamoDB table provisioner — takes AWS credentials as arguments.
Does NOT rely on AWS CLI or ~/.aws/credentials.

Usage:
    python provision-dynamodb-standalone.py \
        --access-key AKIA... \
        --secret-key aBXp... \
        --region us-east-1 \
        --env prod

    python provision-dynamodb-standalone.py \
        --access-key AKIA... \
        --secret-key aBXp... \
        --region us-east-1 \
        --env prod \
        --dry-run

Requirements:
    pip install boto3
"""

import argparse
import logging
import sys

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# Suffix patterns (verified from actual AWS table names)
ENV_SUFFIXES = {
    "prod": {"underscore": "_prod", "dash": "-prod"},
    "staging": {"underscore": "_staging", "dash": "-staging"},
}


def get_table_definitions(env: str) -> list[dict]:
    """
    Return DynamoDB table definitions for tables referenced in config.ini.
    Schemas verified against actual AWS production tables (2026-02-20).
    """
    suffixes = ENV_SUFFIXES.get(env, {"underscore": f"_{env}", "dash": f"-{env}"})
    us = suffixes["underscore"]
    ds = suffixes["dash"]

    # user_details naming quirk: prod="user_details", staging="user_details_test"
    if env == "prod":
        user_details_name = "user_details"
    elif env == "staging":
        user_details_name = "user_details_test"
    else:
        user_details_name = f"user_details_{env}"

    tables = [
        # 1. cortex-users (NextAuth)
        {
            "TableName": "cortex-users",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        # 2. cortex_user_api_keys_{env}
        {
            "TableName": f"cortex_user_api_keys{us}",
            "KeySchema": [
                {"AttributeName": "api_key_id", "KeyType": "HASH"},
                {"AttributeName": "user_email", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "api_key_id", "AttributeType": "S"},
                {"AttributeName": "user_email", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "user_email",
                    "KeySchema": [
                        {"AttributeName": "user_email", "KeyType": "HASH"},
                        {"AttributeName": "api_key_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        # 3. user_metadata_{env}
        {
            "TableName": f"user_metadata{us}",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
        },
        # 4. user_indexed_data_status (not env-suffixed)
        {
            "TableName": "user_indexed_data_status",
            "KeySchema": [
                {"AttributeName": "composite_pk", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "composite_pk", "AttributeType": "S"},
                {"AttributeName": "file_id", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "file_id-index",
                    "KeySchema": [
                        {"AttributeName": "file_id", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        # 5. user_details / user_details_test
        {
            "TableName": user_details_name,
            "KeySchema": [
                {"AttributeName": "email", "KeyType": "HASH"},
                {"AttributeName": "organization", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "email", "AttributeType": "S"},
                {"AttributeName": "organization", "AttributeType": "S"},
                {"AttributeName": "license_key", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
                {"AttributeName": "creation_date", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "license_key-index",
                    "KeySchema": [
                        {"AttributeName": "license_key", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "created_at-organization-index",
                    "KeySchema": [
                        {"AttributeName": "created_at", "KeyType": "HASH"},
                        {"AttributeName": "organization", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "organization-index",
                    "KeySchema": [
                        {"AttributeName": "organization", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "creation_date-index",
                    "KeySchema": [
                        {"AttributeName": "creation_date", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        },
        # 6. users_to_sign_up_{env}
        {
            "TableName": f"users_to_sign_up{us}",
            "KeySchema": [
                {"AttributeName": "email", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "email", "AttributeType": "S"},
            ],
        },
        # 7. tenant-id-mapping-{env}
        {
            "TableName": f"tenant-id-mapping{ds}",
            "KeySchema": [
                {"AttributeName": "Organisation_tenant_id", "KeyType": "HASH"},
                {"AttributeName": "Organisation", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "Organisation_tenant_id", "AttributeType": "S"},
                {"AttributeName": "Organisation", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "Organisation-Organisation_tenant_id-index",
                    "KeySchema": [
                        {"AttributeName": "Organisation", "KeyType": "HASH"},
                        {"AttributeName": "Organisation_tenant_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        # 8. token_bucket_rate_limiter (not env-suffixed)
        {
            "TableName": "token_bucket_rate_limiter",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
            ],
        },
    ]

    return tables


def create_table(client, table_def: dict, dry_run: bool) -> bool:
    """Create a single DynamoDB table. Returns True if created, False if skipped."""
    table_name = table_def["TableName"]

    try:
        client.describe_table(TableName=table_name)
        logger.info("⏭  Table '%s' already exists — skipping", table_name)
        return False
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    if dry_run:
        logger.info("🔍 [DRY RUN] Would create table '%s'", table_name)
        ks = table_def["KeySchema"]
        pk = next(k["AttributeName"] for k in ks if k["KeyType"] == "HASH")
        sk = next((k["AttributeName"] for k in ks if k["KeyType"] == "RANGE"), None)
        logger.info("   PK: %s%s", pk, f", SK: {sk}" if sk else "")
        for gsi in table_def.get("GlobalSecondaryIndexes", []):
            logger.info("   GSI: %s", gsi["IndexName"])
        return True

    create_params = {
        "TableName": table_name,
        "KeySchema": table_def["KeySchema"],
        "AttributeDefinitions": table_def["AttributeDefinitions"],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if "GlobalSecondaryIndexes" in table_def:
        create_params["GlobalSecondaryIndexes"] = table_def["GlobalSecondaryIndexes"]

    logger.info("🔨 Creating table '%s' ...", table_name)
    client.create_table(**create_params)

    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    logger.info("✅ Table '%s' is now ACTIVE", table_name)

    # Enable TTL for token_bucket_rate_limiter
    if table_name == "token_bucket_rate_limiter":
        try:
            client.update_time_to_live(
                TableName=table_name,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "expires_at",
                },
            )
            logger.info("   TTL enabled on 'expires_at'")
        except ClientError as e:
            logger.warning("   Could not enable TTL: %s", e)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision Cortex DynamoDB tables (standalone — no AWS CLI needed)."
    )
    parser.add_argument("--access-key", required=True, help="AWS Access Key ID")
    parser.add_argument("--secret-key", required=True, help="AWS Secret Access Key")
    parser.add_argument("--region", required=True, help="AWS region (e.g. us-east-1)")
    parser.add_argument("--env", required=True, choices=["staging", "prod"], help="Environment")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't create")
    args = parser.parse_args()

    client = boto3.client(
        "dynamodb",
        region_name=args.region,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
    )

    tables = get_table_definitions(args.env)
    logger.info("Provisioning %d tables for env=%s in %s", len(tables), args.env, args.region)
    if args.dry_run:
        logger.info("(DRY RUN — no changes will be made)\n")

    created = 0
    skipped = 0
    errors = 0

    for table_def in tables:
        try:
            if create_table(client, table_def, args.dry_run):
                created += 1
            else:
                skipped += 1
        except Exception as e:
            logger.error("❌ Failed to create '%s': %s", table_def["TableName"], e)
            errors += 1

    verb = "Would create" if args.dry_run else "Created"
    logger.info("\nDone. %s %d table(s), skipped %d, errors %d.", verb, created, skipped, errors)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()