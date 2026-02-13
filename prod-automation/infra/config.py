import json
from dataclasses import dataclass, field
from typing import Optional

import pulumi

from api.models import (
    AddonConfigInput,
    BootstrapNodeGroupConfig,
    EksAccessResolved,
    EksAddonsResolved,
    EksConfigResolved,
    KarpenterConfigResolved,
    KarpenterDisruptionConfig,
    KarpenterNodePoolConfig,
    NatGatewayStrategy,
    SsmAccessNodeConfig,
    SubnetResolved,
    VpcConfigResolved,
    VpcEndpointsResolved,
)


@dataclass
class PulumiCustomerConfig:
    """Customer configuration loaded from Pulumi config for use in infrastructure code."""

    customer_id: str
    environment: str

    # AWS settings
    customer_role_arn: str
    external_id: pulumi.Output[str]
    aws_region: str
    availability_zones: list[str]

    # VPC Configuration (resolved)
    vpc_config: VpcConfigResolved

    # EKS Configuration (resolved)
    eks_config: EksConfigResolved

    # Global tags
    tags: dict[str, str] = field(default_factory=dict)


def _parse_list(value: Optional[str], default: Optional[list[str]] = None) -> list[str]:
    """Parse a comma-separated string into a list."""
    if value is None:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a string boolean value."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _parse_int(value: Optional[str], default: int) -> int:
    """Parse a string integer value."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_json(value: Optional[str], default: dict | list | None = None) -> dict | list | None:
    """Parse a JSON string."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _load_subnets(config: pulumi.Config, prefix: str) -> list[SubnetResolved]:
    """Load subnet configuration from Pulumi config."""
    subnets_json = config.get(f"{prefix}Subnets")
    if not subnets_json:
        return []

    try:
        subnets_data = json.loads(subnets_json)
        return [
            SubnetResolved(
                cidr_block=s["cidr_block"],
                availability_zone=s["availability_zone"],
                name=s["name"],
                tags=s.get("tags", {}),
            )
            for s in subnets_data
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _load_vpc_endpoints(config: pulumi.Config) -> VpcEndpointsResolved:
    """Load VPC endpoints configuration."""
    return VpcEndpointsResolved(
        s3=_parse_bool(config.get("vpcEndpointS3"), True),
        dynamodb=_parse_bool(config.get("vpcEndpointDynamodb"), False),
        ecr_api=_parse_bool(config.get("vpcEndpointEcrApi"), False),
        ecr_dkr=_parse_bool(config.get("vpcEndpointEcrDkr"), False),
        sts=_parse_bool(config.get("vpcEndpointSts"), False),
        logs=_parse_bool(config.get("vpcEndpointLogs"), False),
        ec2=_parse_bool(config.get("vpcEndpointEc2"), False),
        ssm=_parse_bool(config.get("vpcEndpointSsm"), False),
        ssmmessages=_parse_bool(config.get("vpcEndpointSsmMessages"), False),
        ec2messages=_parse_bool(config.get("vpcEndpointEc2Messages"), False),
        elasticloadbalancing=_parse_bool(config.get("vpcEndpointElb"), False),
        autoscaling=_parse_bool(config.get("vpcEndpointAutoscaling"), False),
    )


def _load_vpc_config(config: pulumi.Config) -> VpcConfigResolved:
    """Load VPC configuration from Pulumi config."""
    vpc_cidr = config.get("vpcCidr") or "10.0.0.0/16"
    secondary_cidrs = _parse_list(config.get("secondaryCidrBlocks"), [])
    nat_strategy_str = config.get("natGatewayStrategy") or "single"
    nat_strategy = NatGatewayStrategy(nat_strategy_str)

    public_subnets = _load_subnets(config, "public")
    private_subnets = _load_subnets(config, "private")
    pod_subnets = _load_subnets(config, "pod")

    vpc_endpoints = _load_vpc_endpoints(config)

    return VpcConfigResolved(
        cidr_block=vpc_cidr,
        secondary_cidr_blocks=secondary_cidrs,
        nat_gateway_strategy=nat_strategy,
        public_subnets=public_subnets,
        private_subnets=private_subnets,
        pod_subnets=pod_subnets,
        vpc_endpoints=vpc_endpoints,
        enable_dns_hostnames=_parse_bool(config.get("enableDnsHostnames"), True),
        enable_dns_support=_parse_bool(config.get("enableDnsSupport"), True),
        tags=dict(_parse_json(config.get("vpcTags"), {}) or {}),
    )


def _load_ssm_access_node(config: pulumi.Config) -> Optional[SsmAccessNodeConfig]:
    """Load SSM access node configuration."""
    enabled = _parse_bool(config.get("ssmAccessNodeEnabled"), False)
    if not enabled:
        return None

    return SsmAccessNodeConfig(
        enabled=True,
        instance_type=config.get("ssmAccessNodeInstanceType") or "t3.micro",
    )


def _load_eks_access(config: pulumi.Config) -> EksAccessResolved:
    """Load EKS access configuration."""
    ssm_access_node = _load_ssm_access_node(config)

    return EksAccessResolved(
        endpoint_private_access=_parse_bool(config.get("endpointPrivateAccess"), True),
        endpoint_public_access=_parse_bool(config.get("endpointPublicAccess"), False),
        public_access_cidrs=_parse_list(config.get("publicAccessCidrs"), []),
        authentication_mode=config.get("authenticationMode") or "API_AND_CONFIG_MAP",
        bootstrap_cluster_creator_admin_permissions=_parse_bool(
            config.get("bootstrapClusterCreatorAdmin"), True
        ),
        access_entries=[],  # Access entries handled separately if needed
        ssm_access_node=ssm_access_node,
    )


def _get_default_addon_config(enabled: bool = True) -> AddonConfigInput:
    """Get default addon configuration."""
    return AddonConfigInput(
        enabled=enabled,
        version=None,
        service_account_role_arn=None,
        configuration={},
        resolve_conflicts_on_create="OVERWRITE",
        resolve_conflicts_on_update="PRESERVE",
    )


def _load_eks_addons(config: pulumi.Config) -> EksAddonsResolved:
    """Load EKS addons configuration."""
    return EksAddonsResolved(
        vpc_cni=_get_default_addon_config(_parse_bool(config.get("addonVpcCni"), True)),
        coredns=_get_default_addon_config(_parse_bool(config.get("addonCoredns"), True)),
        kube_proxy=_get_default_addon_config(_parse_bool(config.get("addonKubeProxy"), True)),
        ebs_csi_driver=_get_default_addon_config(_parse_bool(config.get("addonEbsCsi"), True)),
        efs_csi_driver=_get_default_addon_config(_parse_bool(config.get("addonEfsCsi"), False)),
        pod_identity_agent=_get_default_addon_config(
            _parse_bool(config.get("addonPodIdentity"), True)
        ),
        snapshot_controller=_get_default_addon_config(
            _parse_bool(config.get("addonSnapshot"), False)
        ),
    )


def _load_bootstrap_node_group(config: pulumi.Config) -> BootstrapNodeGroupConfig:
    """Load bootstrap node group configuration."""
    instance_types = _parse_list(config.get("bootstrapInstanceTypes"), ["t3.medium"])

    # Parse labels from JSON
    labels_json = config.get("bootstrapLabels")
    labels = {"node-role": "system"}
    if labels_json:
        try:
            parsed_labels = json.loads(labels_json)
            if isinstance(parsed_labels, dict):
                labels.update(parsed_labels)
        except json.JSONDecodeError:
            pass

    return BootstrapNodeGroupConfig(
        instance_types=instance_types,
        desired_size=_parse_int(config.get("bootstrapDesiredSize"), 2),
        min_size=_parse_int(config.get("bootstrapMinSize"), 2),
        max_size=_parse_int(config.get("bootstrapMaxSize"), 3),
        disk_size=_parse_int(config.get("bootstrapDiskSize"), 50),
        labels=labels,
    )


def _load_karpenter_node_pool(config: pulumi.Config) -> KarpenterNodePoolConfig:
    """Load Karpenter NodePool configuration."""
    return KarpenterNodePoolConfig(
        instance_families=_parse_list(
            config.get("karpenterInstanceFamilies"),
            ["t3", "t3a", "m5", "m5a", "c5", "c5a"],
        ),
        instance_sizes=_parse_list(
            config.get("karpenterInstanceSizes"),
            ["medium", "large", "xlarge", "2xlarge"],
        ),
        capacity_types=_parse_list(
            config.get("karpenterCapacityTypes"),
            ["spot", "on-demand"],
        ),
        architectures=_parse_list(
            config.get("karpenterArchitectures"),
            ["amd64"],
        ),
        cpu_limit=_parse_int(config.get("karpenterCpuLimit"), 1000),
        memory_limit_gb=_parse_int(config.get("karpenterMemoryLimitGb"), 1000),
    )


def _load_karpenter_disruption(config: pulumi.Config) -> KarpenterDisruptionConfig:
    """Load Karpenter disruption configuration."""
    return KarpenterDisruptionConfig(
        consolidation_policy=config.get("karpenterConsolidationPolicy") or "WhenEmptyOrUnderutilized",
        consolidate_after_seconds=_parse_int(config.get("karpenterConsolidateAfterSeconds"), 30),
    )


def _load_karpenter_config(config: pulumi.Config) -> KarpenterConfigResolved:
    """Load Karpenter configuration."""
    return KarpenterConfigResolved(
        version=config.get("karpenterVersion") or "1.8.2",
        node_pool=_load_karpenter_node_pool(config),
        disruption=_load_karpenter_disruption(config),
    )


def _load_eks_config(config: pulumi.Config) -> EksConfigResolved:
    """Load EKS configuration from Pulumi config."""
    eks_version = config.get("eksVersion") or "1.31"
    service_cidr = config.get("serviceIpv4Cidr") or "172.20.0.0/16"

    access = _load_eks_access(config)
    addons = _load_eks_addons(config)
    bootstrap_node_group = _load_bootstrap_node_group(config)
    karpenter = _load_karpenter_config(config)

    logging_enabled = _parse_bool(config.get("loggingEnabled"), False)
    logging_types = (
        _parse_list(config.get("loggingTypes"), ["api", "audit", "authenticator"])
        if logging_enabled
        else []
    )

    return EksConfigResolved(
        version=eks_version,
        service_ipv4_cidr=service_cidr,
        access=access,
        bootstrap_node_group=bootstrap_node_group,
        karpenter=karpenter,
        addons=addons,
        logging_enabled=logging_enabled,
        logging_types=logging_types,
        encryption_enabled=_parse_bool(config.get("encryptionEnabled"), True),
        encryption_kms_key_arn=config.get("encryptionKmsKeyArn"),
        zonal_shift_enabled=_parse_bool(config.get("zonalShiftEnabled"), False),
        tags=dict(_parse_json(config.get("eksTags"), {}) or {}),
    )


def load_customer_config() -> PulumiCustomerConfig:
    """Load customer configuration from Pulumi config."""
    config = pulumi.Config()

    # Basic settings
    customer_id = config.require("customerId")
    environment = config.get("environment") or "prod"
    customer_role_arn = config.require("customerRoleArn")
    external_id = config.require_secret("externalId")
    aws_region = config.get("awsRegion") or "us-east-1"

    # Availability zones
    az_config = config.get("availabilityZones")
    if az_config:
        availability_zones = [az.strip() for az in az_config.split(",")]
    else:
        availability_zones = [f"{aws_region}a", f"{aws_region}b", f"{aws_region}c"]

    # Load VPC and EKS configs
    vpc_config = _load_vpc_config(config)
    eks_config = _load_eks_config(config)

    # Global tags
    tags: dict[str, str] = {
        "Environment": environment,
        "Customer": customer_id,
        "ManagedBy": "pulumi",
    }
    custom_tags = _parse_json(config.get("tags"), {})
    if custom_tags and isinstance(custom_tags, dict):
        tags.update(custom_tags)

    return PulumiCustomerConfig(
        customer_id=customer_id,
        environment=environment,
        customer_role_arn=customer_role_arn,
        external_id=external_id,
        aws_region=aws_region,
        availability_zones=availability_zones,
        vpc_config=vpc_config,
        eks_config=eks_config,
        tags=tags,
    )