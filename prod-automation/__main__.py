import pulumi
import pulumi_aws as aws

from infra.components.eks import EksCluster
from infra.components.iam import EksIamRoles
from infra.components.networking import Networking
from infra.config import load_customer_config
from infra.providers import create_customer_aws_provider
from infra.components.access_node import AccessNode

import json

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



# =============================================================================
# External Secrets Operator (ESO) - IAM Role (IRSA)
# =============================================================================

eso_policy = aws.iam.Policy(
    f"{config.customer_id}-eso-policy",
    policy=json.dumps({
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
    }),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

eso_role = aws.iam.Role(
    f"{config.customer_id}-eso-role",
    assume_role_policy=pulumi.Output.all(
        eks.oidc_provider_arn, eks.oidc_provider_url
    ).apply(
        lambda args: json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Federated": args[0]},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{args[1].replace('https://', '')}:sub": "system:serviceaccount:external-secrets:external-secrets",
                        f"{args[1].replace('https://', '')}:aud": "sts.amazonaws.com",
                    }
                },
            }],
        })
    ),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.iam.RolePolicyAttachment(
    f"{config.customer_id}-eso-policy-attach",
    role=eso_role.name,
    policy_arn=eso_policy.arn,
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

cortex_app_secret = aws.secretsmanager.Secret(
    f"{config.customer_id}-cortex-app-secrets",
    name=f"/byoc/{config.customer_id}/cortex-app",
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
    ).apply(lambda args: json.dumps({
        "FALKORDB_PASSWORD": args[0],
        "MILVUS_TOKEN": args[1],
        "GOOGLE_API_KEY": _eso_google_key,
        "GEMINI_API_KEY": _eso_gemini_key,
    })),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

cortex_ingestion_secret = aws.secretsmanager.Secret(
    f"{config.customer_id}-cortex-ingestion-secrets",
    name=f"/byoc/{config.customer_id}/cortex-ingestion",
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

aws.secretsmanager.SecretVersion(
    f"{config.customer_id}-cortex-ingestion-secrets-version",
    secret_id=cortex_ingestion_secret.id,
    secret_string=pulumi.Output.all(
        pulumi_config.require_secret("esoFalkordbPassword"),
        pulumi_config.require_secret("esoMilvusToken"),
    ).apply(lambda args: json.dumps({
        "FALKORDB_PASSWORD": args[0],
        "MILVUS_TOKEN": args[1],
        "GOOGLE_API_KEY": _eso_google_key,
        "GEMINI_API_KEY": _eso_gemini_key,
    })),
    opts=pulumi.ResourceOptions(provider=aws_provider),
)

pulumi.export("eso_role_arn", eso_role.arn)
pulumi.export("cortex_app_secret_arn", cortex_app_secret.arn)
pulumi.export("cortex_ingestion_secret_arn", cortex_ingestion_secret.arn)



access_node = None
if (
    config.eks_config.access.ssm_access_node
    and config.eks_config.access.ssm_access_node.enabled
):
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
    # Grant the access node's IAM role Kubernetes cluster-admin permissions
    # This allows users SSM'd into the access node to run kubectl commands
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