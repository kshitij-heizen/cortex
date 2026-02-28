import json

import pulumi
import pulumi_aws as aws

from infra.components.access_node import AccessNode
from infra.components.eks import EksCluster
from infra.components.iam import EksIamRoles
from infra.components.networking import Networking
from infra.config import load_customer_config
from infra.providers import create_customer_aws_provider

pulumi_config = pulumi.Config()
config = load_customer_config()

aws_provider = create_customer_aws_provider(config)


networking = Networking(
    name=config.customer_id,
    vpc_config=config.vpc_config,
    availability_zones=config.availability_zones,
    provider=aws_provider,
    tags=config.tags,
)


iam = EksIamRoles(
    name=config.customer_id,
    provider=aws_provider,
    opts=pulumi.ResourceOptions(depends_on=[networking]),
)


eks = EksCluster(
    name=config.customer_id,
    vpc_id=networking.vpc_id,
    vpc_cidr=config.vpc_config.cidr_block,
    private_subnet_ids=networking.private_subnet_ids,
    public_subnet_ids=networking.public_subnet_ids,
    cluster_role_arn=iam.cluster_role_arn,
    node_role_arn=iam.node_role_arn,
    node_instance_profile_arn=iam.node_instance_profile_arn,
    eks_config=config.eks_config,
    provider=aws_provider,
    tags=config.tags,
    opts=pulumi.ResourceOptions(depends_on=[iam]),
)


eso_policy = aws.iam.Policy(
    f"{config.customer_id}-eso-policy",
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret",
                    ],
                    "Resource": f"arn:aws:secretsmanager:{config.aws_region}:*:secret:/byoc/{config.customer_id}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:ListSecrets"],
                    "Resource": "*",
                },
            ],
        }
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

eso_role = aws.iam.Role(
    f"{config.customer_id}-eso-role",
    assume_role_policy=pulumi.Output.all(eks.oidc_provider_arn, eks.oidc_provider_url).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": args[0]},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                f"{args[1].replace('https://', '')}:sub": "system:serviceaccount:external-secrets:external-secrets",
                                f"{args[1].replace('https://', '')}:aud": "sts.amazonaws.com",
                            }
                        },
                    }
                ],
            }
        )
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.iam.RolePolicyAttachment(
    f"{config.customer_id}-eso-policy-attach",
    role=eso_role.name,
    policy_arn=eso_policy.arn,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)


documents_bucket = aws.s3.BucketV2(
    f"{config.customer_id}-documents-bucket",
    tags={**config.tags, "Name": f"{config.customer_id}-documents"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.s3.BucketPublicAccessBlock(
    f"{config.customer_id}-documents-bucket-public-access",
    bucket=documents_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

local_sources_bucket = aws.s3.BucketV2(
    f"{config.customer_id}-local-sources-bucket",
    tags={**config.tags, "Name": f"{config.customer_id}-cortex-local-sources"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.s3.BucketPublicAccessBlock(
    f"{config.customer_id}-local-sources-bucket-public-access",
    bucket=local_sources_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)


# =============================================================================
# DynamoDB Tables
# Schemas verified against production AWS tables (2026-02-20).
# =============================================================================

_env = config.environment  # "prod" or "staging"
_env_us = f"_{_env}"  # "_prod"
_env_ds = f"-{_env}"  # "-prod"
_ddb_tags = {**config.tags, "ManagedBy": "pulumi"}

# --- cortex-users (NextAuth) ---
# PK=pk, SK=sk, GSI1(GSI1PK/GSI1SK)
cortex_users_table = aws.dynamodb.Table(
    f"{config.customer_id}-cortex-users",
    name="cortex-users",
    billing_mode="PAY_PER_REQUEST",
    hash_key="pk",
    range_key="sk",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="pk", type="S"),
        aws.dynamodb.TableAttributeArgs(name="sk", type="S"),
        aws.dynamodb.TableAttributeArgs(name="GSI1PK", type="S"),
        aws.dynamodb.TableAttributeArgs(name="GSI1SK", type="S"),
    ],
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="GSI1",
            hash_key="GSI1PK",
            range_key="GSI1SK",
            projection_type="ALL",
        ),
    ],
    tags={**_ddb_tags, "Name": "cortex-users"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- cortex_user_api_keys_{env} ---
# PK=api_key_id, SK=user_email, GSI: user_email(PK=user_email, SK=api_key_id)
api_keys_table = aws.dynamodb.Table(
    f"{config.customer_id}-cortex-api-keys",
    name=f"cortex_user_api_keys{_env_us}",
    billing_mode="PAY_PER_REQUEST",
    hash_key="api_key_id",
    range_key="user_email",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="api_key_id", type="S"),
        aws.dynamodb.TableAttributeArgs(name="user_email", type="S"),
    ],
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="user_email",
            hash_key="user_email",
            range_key="api_key_id",
            projection_type="ALL",
        ),
    ],
    tags={**_ddb_tags, "Name": f"cortex_user_api_keys{_env_us}"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- user_metadata_{env} ---
# PK=user_id
user_metadata_table = aws.dynamodb.Table(
    f"{config.customer_id}-user-metadata",
    name=f"user_metadata{_env_us}",
    billing_mode="PAY_PER_REQUEST",
    hash_key="user_id",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="user_id", type="S"),
    ],
    tags={**_ddb_tags, "Name": f"user_metadata{_env_us}"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- user_indexed_data_status ---
# PK=composite_pk, GSI: file_id-index(PK=file_id)
user_indexed_data_table = aws.dynamodb.Table(
    f"{config.customer_id}-user-indexed-data-status",
    name="user_indexed_data_status",
    billing_mode="PAY_PER_REQUEST",
    hash_key="composite_pk",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="composite_pk", type="S"),
        aws.dynamodb.TableAttributeArgs(name="file_id", type="S"),
    ],
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="file_id-index",
            hash_key="file_id",
            projection_type="ALL",
        ),
    ],
    tags={**_ddb_tags, "Name": "user_indexed_data_status"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- user_details ---
# PK=email, SK=organization, 4 GSIs
user_details_table = aws.dynamodb.Table(
    f"{config.customer_id}-user-details",
    name="user_details",
    billing_mode="PAY_PER_REQUEST",
    hash_key="email",
    range_key="organization",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="email", type="S"),
        aws.dynamodb.TableAttributeArgs(name="organization", type="S"),
        aws.dynamodb.TableAttributeArgs(name="license_key", type="S"),
        aws.dynamodb.TableAttributeArgs(name="created_at", type="S"),
        aws.dynamodb.TableAttributeArgs(name="creation_date", type="S"),
    ],
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="license_key-index",
            hash_key="license_key",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="created_at-organization-index",
            hash_key="created_at",
            range_key="organization",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="organization-index",
            hash_key="organization",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="creation_date-index",
            hash_key="creation_date",
            projection_type="ALL",
        ),
    ],
    tags={**_ddb_tags, "Name": "user_details"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- users_to_sign_up_{env} ---
# PK=email
users_to_sign_up_table = aws.dynamodb.Table(
    f"{config.customer_id}-users-to-sign-up",
    name=f"users_to_sign_up{_env_us}",
    billing_mode="PAY_PER_REQUEST",
    hash_key="email",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="email", type="S"),
    ],
    tags={**_ddb_tags, "Name": f"users_to_sign_up{_env_us}"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- tenant-id-mapping-{env} ---
# PK=Organisation_tenant_id, SK=Organisation, GSI
tenant_mapping_table = aws.dynamodb.Table(
    f"{config.customer_id}-tenant-id-mapping",
    name=f"tenant-id-mapping{_env_ds}",
    billing_mode="PAY_PER_REQUEST",
    hash_key="Organisation_tenant_id",
    range_key="Organisation",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="Organisation_tenant_id", type="S"),
        aws.dynamodb.TableAttributeArgs(name="Organisation", type="S"),
    ],
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="Organisation-Organisation_tenant_id-index",
            hash_key="Organisation",
            range_key="Organisation_tenant_id",
            projection_type="ALL",
        ),
    ],
    tags={**_ddb_tags, "Name": f"tenant-id-mapping{_env_ds}"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# --- token_bucket_rate_limiter ---
# PK=pk, TTL on expires_at
token_bucket_table = aws.dynamodb.Table(
    f"{config.customer_id}-token-bucket-rate-limiter",
    name="token_bucket_rate_limiter",
    billing_mode="PAY_PER_REQUEST",
    hash_key="pk",
    attributes=[
        aws.dynamodb.TableAttributeArgs(name="pk", type="S"),
    ],
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="expires_at",
        enabled=True,
    ),
    tags={**_ddb_tags, "Name": "token_bucket_rate_limiter"},
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# Collect all table ARNs (including GSI ARNs) for IAM policy
_all_table_arns = [
    cortex_users_table.arn,
    api_keys_table.arn,
    user_metadata_table.arn,
    user_indexed_data_table.arn,
    user_details_table.arn,
    users_to_sign_up_table.arn,
    tenant_mapping_table.arn,
    token_bucket_table.arn,
]


# =============================================================================
# IAM - Cortex App Role (IRSA) with fixed name for predictable ARN
# =============================================================================

cortex_app_policy = aws.iam.Policy(
    f"{config.customer_id}-cortex-app-policy",
    policy=pulumi.Output.all(
        documents_bucket.arn,
        local_sources_bucket.arn,
        *_all_table_arns,
    ).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            args[0],
                            f"{args[0]}/*",
                            args[1],
                            f"{args[1]}/*",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                            "dynamodb:BatchGetItem",
                            "dynamodb:BatchWriteItem",
                            "dynamodb:DescribeTable",
                            "dynamodb:CreateTable",
                            "dynamodb:UpdateTable",
                            "dynamodb:DescribeTimeToLive",
                            "dynamodb:UpdateTimeToLive",
                            "dynamodb:ConditionCheckItem",
                            "dynamodb:ListTagsOfResource",
                        ],
                        # Table ARNs + index ARNs (table/*/index/*)
                        "Resource": [arn for base in args[2:] for arn in (base, f"{base}/index/*")],
                    },
                ],
            }
        )
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

# Fixed role name so the ARN is predictable for helm values
_cortex_app_role_name = f"{config.customer_id}-cortex-app-role"

cortex_app_role = aws.iam.Role(
    f"{config.customer_id}-cortex-app-role",
    name=_cortex_app_role_name,
    assume_role_policy=pulumi.Output.all(eks.oidc_provider_arn, eks.oidc_provider_url).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": args[0]},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringLike": {
                                f"{args[1].replace('https://', '')}:sub": "system:serviceaccount:cortex-*:cortex-*",
                            },
                            "StringEquals": {
                                f"{args[1].replace('https://', '')}:aud": "sts.amazonaws.com",
                            },
                        },
                    }
                ],
            }
        )
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.iam.RolePolicyAttachment(
    f"{config.customer_id}-cortex-app-policy-attach",
    role=cortex_app_role.name,
    policy_arn=cortex_app_policy.arn,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)


# =============================================================================
# Secrets Manager - cortex-app and cortex-ingestion secrets
# =============================================================================

cortex_app_secret = aws.secretsmanager.Secret(
    f"{config.customer_id}-cortex-app-secrets",
    name=f"/byoc/{config.customer_id}/cortex-app",
    recovery_window_in_days=0,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

_eso_google_key = pulumi_config.get("esoGoogleApiKey") or ""
_eso_gemini_key = pulumi_config.get("esoGeminiApiKey") or ""

aws.secretsmanager.SecretVersion(
    f"{config.customer_id}-cortex-app-secrets-version",
    secret_id=cortex_app_secret.id,
    secret_string=pulumi.Output.all(
        pulumi_config.require_secret("esoFalkordbPassword"),
        pulumi_config.require_secret("esoMilvusToken"),
        documents_bucket.bucket,
        local_sources_bucket.bucket,
        cortex_users_table.name,
        api_keys_table.name,
        tenant_mapping_table.name,
        cortex_app_role.arn,
        user_metadata_table.name,
        user_indexed_data_table.name,
        user_details_table.name,
        users_to_sign_up_table.name,
        token_bucket_table.name,
    ).apply(
        lambda args: json.dumps(
            {
                "FALKORDB_PASSWORD": args[0],
                "MILVUS_TOKEN": args[1],
                "GOOGLE_API_KEY": _eso_google_key,
                "GEMINI_API_KEY": _eso_gemini_key,
                "MINIO_BUCKET": args[2],
                "CORTEX_LOCAL_SOURCES_BUCKET_NAME": args[3],
                "NEXTAUTH_TABLE_NAME": args[4],
                "CORTEX_API_KEYS_TABLE_NAME": args[5],
                "TENANT_ID_MAPPING_TABLE_NAME": args[6],
                "CORTEX_APP_ROLE_ARN": args[7],
                "USER_METADATA_TABLE_NAME": args[8],
                "USER_INDEXED_DATA_TABLE": args[9],
                "USER_DETAILS_TABLE_NAME": args[10],
                "USERS_TO_SIGN_UP_TABLE_NAME": args[11],
                "TOKEN_BUCKET_TABLE_NAME": args[12],
            }
        )
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

cortex_ingestion_secret = aws.secretsmanager.Secret(
    f"{config.customer_id}-cortex-ingestion-secrets",
    name=f"/byoc/{config.customer_id}/cortex-ingestion",
    recovery_window_in_days=0,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.secretsmanager.SecretVersion(
    f"{config.customer_id}-cortex-ingestion-secrets-version",
    secret_id=cortex_ingestion_secret.id,
    secret_string=pulumi.Output.all(
        pulumi_config.require_secret("esoFalkordbPassword"),
        pulumi_config.require_secret("esoMilvusToken"),
        documents_bucket.bucket,
        api_keys_table.name,
        cortex_app_role.arn,
        user_indexed_data_table.name,
        token_bucket_table.name,
    ).apply(
        lambda args: json.dumps(
            {
                "FALKORDB_PASSWORD": args[0],
                "MILVUS_TOKEN": args[1],
                "GOOGLE_API_KEY": _eso_google_key,
                "GEMINI_API_KEY": _eso_gemini_key,
                "MINIO_BUCKET": args[2],
                "CORTEX_API_KEYS_TABLE_NAME": args[3],
                "CORTEX_APP_ROLE_ARN": args[4],
                "USER_INDEXED_DATA_TABLE": args[5],
                "TOKEN_BUCKET_TABLE_NAME": args[6],
            }
        )
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)


# =============================================================================
# Access Node (SSM)
# =============================================================================

access_node = None
if config.eks_config.access.ssm_access_node and config.eks_config.access.ssm_access_node.enabled:

    def get_first_subnet(ids: list[str]) -> str:
        if not ids:
            raise ValueError("No private subnets available for access node")
        return ids[0]

    access_node = AccessNode(
        name=config.customer_id,
        vpc_id=networking.vpc_id,
        subnet_id=networking.private_subnet_ids.apply(get_first_subnet),
        cluster_security_group_id=eks.cluster_security_group_id,
        cluster_name=eks.cluster_name,
        region=config.aws_region,
        instance_type=config.eks_config.access.ssm_access_node.instance_type,
        provider=aws_provider,
        tags=config.tags,
        opts=pulumi.ResourceOptions(depends_on=[eks]),
    )


if access_node:
    aws.eks.AccessEntry(
        f"{config.customer_id}-access-node-eks-access",
        cluster_name=eks.cluster_name,
        principal_arn=access_node.role.arn,
        type="STANDARD",
        opts=pulumi.ResourceOptions(
            provider=aws_provider,
            depends_on=[eks, access_node],
        ),
    )

    aws.eks.AccessPolicyAssociation(
        f"{config.customer_id}-access-node-eks-policy",
        cluster_name=eks.cluster_name,
        principal_arn=access_node.role.arn,
        policy_arn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
        access_scope=aws.eks.AccessPolicyAssociationAccessScopeArgs(
            type="cluster",
        ),
        opts=pulumi.ResourceOptions(
            provider=aws_provider,
            depends_on=[eks, access_node],
        ),
    )

    pulumi.export("access_node_instance_id", access_node.instance_id)
    pulumi.export("access_node_private_ip", access_node.private_ip)
    pulumi.export("access_node_role_arn", access_node.role.arn)


# =============================================================================
# Exports
# =============================================================================

pulumi.export("vpc_id", networking.vpc_id)
pulumi.export("private_subnet_ids", networking.private_subnet_ids)
pulumi.export("public_subnet_ids", networking.public_subnet_ids)
pulumi.export("pod_subnet_ids", networking.pod_subnet_ids)

pulumi.export("eks_cluster_role_arn", iam.cluster_role_arn)
pulumi.export("eks_node_role_arn", iam.node_role_arn)
pulumi.export("eks_node_role_name", iam.node_role_name)
pulumi.export("eks_node_instance_profile_arn", iam.node_instance_profile_arn)

pulumi.export("karpenter_controller_role_arn", eks.karpenter_controller_role.arn)

pulumi.export("eks_cluster_name", eks.cluster_name)
pulumi.export("eks_cluster_endpoint", eks.cluster_endpoint)
pulumi.export("eks_cluster_arn", eks.cluster_arn)
pulumi.export("eks_oidc_provider_arn", eks.oidc_provider_arn)
pulumi.export("eks_oidc_provider_url", eks.oidc_provider_url)

pulumi.export("eso_role_arn", eso_role.arn)
pulumi.export("cortex_app_secret_arn", cortex_app_secret.arn)
pulumi.export("cortex_ingestion_secret_arn", cortex_ingestion_secret.arn)

pulumi.export("documents_bucket_name", documents_bucket.bucket)
pulumi.export("local_sources_bucket_name", local_sources_bucket.bucket)
pulumi.export("cortex_users_table_name", cortex_users_table.name)
pulumi.export("api_keys_table_name", api_keys_table.name)
pulumi.export("user_metadata_table_name", user_metadata_table.name)
pulumi.export("user_indexed_data_table_name", user_indexed_data_table.name)
pulumi.export("user_details_table_name", user_details_table.name)
pulumi.export("users_to_sign_up_table_name", users_to_sign_up_table.name)
pulumi.export("tenant_mapping_table_name", tenant_mapping_table.name)
pulumi.export("token_bucket_table_name", token_bucket_table.name)
pulumi.export("cortex_app_role_arn", cortex_app_role.arn)
pulumi.export("cortex_app_role_name", cortex_app_role.name)

pulumi.export(
    "config_summary",
    {
        "customer_id": config.customer_id,
        "environment": config.environment,
        "aws_region": config.aws_region,
        "eks_version": config.eks_config.version,
        "endpoint_private_access": config.eks_config.access.endpoint_private_access,
        "endpoint_public_access": config.eks_config.access.endpoint_public_access,
        "nat_gateway_strategy": config.vpc_config.nat_gateway_strategy.value,
        "service_cidr": config.eks_config.service_ipv4_cidr,
        "bootstrap_node_group": {
            "instance_types": config.eks_config.bootstrap_node_group.instance_types,
            "desired_size": config.eks_config.bootstrap_node_group.desired_size,
            "min_size": config.eks_config.bootstrap_node_group.min_size,
            "max_size": config.eks_config.bootstrap_node_group.max_size,
        },
        "karpenter": {
            "version": config.eks_config.karpenter.version,
            "instance_families": config.eks_config.karpenter.node_pool.instance_families,
            "capacity_types": config.eks_config.karpenter.node_pool.capacity_types,
            "cpu_limit": config.eks_config.karpenter.node_pool.cpu_limit,
            "memory_limit_gb": config.eks_config.karpenter.node_pool.memory_limit_gb,
        },
    },
)
