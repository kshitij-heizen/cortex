#!/usr/bin/env python3
"""
Provision all DynamoDB tables required by Cortex services.

Tables are sourced from cortex-application/config.ini and cortex-ingestion.
Schemas verified against actual AWS production tables.

Usage:
    python provision-dynamodb-tables.py --region us-east-1 --env prod
    python provision-dynamodb-tables.py --region us-east-1 --env staging --dry-run

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


# Mapping of env -> suffix patterns (verified from actual AWS table names)
ENV_SUFFIXES = {
    "prod": {"underscore": "_prod", "dash": "-prod"},
    "staging": {"underscore": "_staging", "dash": "-staging"},
}

# Special case: user_details uses _test for staging, not _staging
USER_DETAILS_STAGING_SUFFIX = "_test"


def get_table_definitions(env: str) -> list[dict]:
    """
    Return DynamoDB table definitions for tables referenced in config.ini.
    Schemas verified against actual AWS production tables (2026-02-20).
    """
    suffixes = ENV_SUFFIXES.get(
        env, {"underscore": f"_{env}", "dash": f"-{env}"})
    us = suffixes["underscore"]  # e.g. _prod
    ds = suffixes["dash"]  # e.g. -prod

    # user_details has inconsistent naming: prod=user_details, staging=user_details_test
    if env == "prod":
        user_details_name = "user_details"
    elif env == "staging":
        user_details_name = f"user_details{USER_DETAILS_STAGING_SUFFIX}"
    else:
        user_details_name = f"user_details_{env}"

    tables = [
        # ------------------------------------------------------------------ #
        # cortex-users (NextAuth users table)
        # AWS verified: PK=pk, SK=sk, GSI1(GSI1PK/GSI1SK)
        # Not env-suffixed
        # ------------------------------------------------------------------ #
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
        # ------------------------------------------------------------------ #
        # cortex_user_api_keys_{env}
        # Source: cortex-application/config.ini → CORTEX_API_KEYS_TABLE_NAME
        # AWS verified: PK=api_key_id, SK=user_email
        #   GSI: user_email (PK=user_email, SK=api_key_id)
        # ------------------------------------------------------------------ #
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
        # ------------------------------------------------------------------ #
        # user_metadata_{env}
        # Source: cortex-application/config.ini → USER_METADATA_TABLE_NAME
        # AWS verified: PK=user_id (no SK, no GSI)
        # ------------------------------------------------------------------ #
        {
            "TableName": f"user_metadata{us}",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
        },
        # ------------------------------------------------------------------ #
        # user_indexed_data_status
        # Source: cortex-application/config.ini → USER_INDEXED_DATA_TABLE
        # AWS verified: PK=composite_pk
        #   GSI: file_id-index (PK=file_id)
        # Note: NOT env-suffixed
        # ------------------------------------------------------------------ #
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
        # ------------------------------------------------------------------ #
        # user_details / user_details_test
        # Source: cortex-application/config.ini → USER_DETAILS_TABLE_NAME
        # AWS verified: PK=email, SK=organization
        #   GSIs: license_key-index, created_at-organization-index,
        #         organization-index, creation_date-index
        # Note: prod = "user_details", staging = "user_details_test"
        # ------------------------------------------------------------------ #
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
        # ------------------------------------------------------------------ #
        # users_to_sign_up_{env}
        # Source: cortex-application/config.ini → USERS_TO_SIGN_UP_TABLE_NAME
        # AWS verified: PK=email (no SK, no GSI)
        # ------------------------------------------------------------------ #
        {
            "TableName": f"users_to_sign_up{us}",
            "KeySchema": [
                {"AttributeName": "email", "KeyType": "HASH"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "email", "AttributeType": "S"},
            ],
        },
        # ------------------------------------------------------------------ #
        # tenant-id-mapping-{env}
        # Source: cortex-application/config.ini → TENANT_ID_MAPPING_TABLE_NAME
        # AWS verified: PK=Organisation_tenant_id, SK=Organisation
        #   GSI: Organisation-Organisation_tenant_id-index
        #        (PK=Organisation, SK=Organisation_tenant_id)
        # ------------------------------------------------------------------ #
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
                        {"AttributeName": "Organisation_tenant_id",
                            "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        # ------------------------------------------------------------------ #
        # token_bucket_rate_limiter
        # Source: cortex-ingestion/utils/ratelimits.py
        # AWS: does NOT exist yet (new table)
        # Schema from code: PK=pk, TTL on expires_at
        # Note: NOT env-suffixed
        # ------------------------------------------------------------------ #
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
    """
    Create a single DynamoDB table. Returns True if created, False if skipped.
    """
    table_name = table_def["TableName"]

    # Check if table already exists
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
        sk = next((k["AttributeName"]
                  for k in ks if k["KeyType"] == "RANGE"), None)
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

    # Wait for table to become active
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
            logger.info("   TTL enabled on 'expires_at' for '%s'", table_name)
        except ClientError as e:
            logger.warning(
                "   Could not enable TTL for '%s': %s", table_name, e)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision Cortex DynamoDB tables (from config.ini)."
    )
    parser.add_argument(
        "--region",
        required=True,
        help="AWS region (e.g. us-east-1)",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["staging", "prod"],
        help="Deployment environment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making changes",
    )
    args = parser.parse_args()

    client = boto3.client("dynamodb", region_name=args.region)
    tables = get_table_definitions(args.env)

    logger.info("Provisioning %d tables for env=%s in %s",
                len(tables), args.env, args.region)
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
            logger.error("❌ Failed to create '%s': %s",
                         table_def["TableName"], e)
            errors += 1

    verb = "Would create" if args.dry_run else "Created"
    logger.info(
        "\nDone. %s %d table(s), skipped %d, errors %d.",
        verb,
        created,
        skipped,
        errors,
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
