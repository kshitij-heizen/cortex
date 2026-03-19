"""Pulumi Automation API engine for local deployments with S3 backend."""

import json
import logging
import os
from pathlib import Path
from typing import Any

import pulumi.automation as auto

from api.models import CustomerConfigResolved

logger = logging.getLogger(__name__)


class PulumiEngine:
    """Run Pulumi operations locally using the Automation API with an S3 state backend."""

    def __init__(
        self,
        backend_url: str,
        secrets_provider: str,
        work_dir: str,
    ):
        self.backend_url = backend_url
        self.secrets_provider = secrets_provider
        self.work_dir = str(Path(work_dir).resolve())

    def _get_stack(self, stack_name: str) -> auto.Stack:
        """Create or select a Pulumi stack backed by S3."""
        env_vars = {
            "PULUMI_BACKEND_URL": self.backend_url,
            "PULUMI_CONFIG_PASSPHRASE": os.environ.get("PULUMI_CONFIG_PASSPHRASE", ""),
        }

        # Pass AWS creds so the Pulumi engine can assume customer roles
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ):
            val = os.environ.get(key)
            if val:
                env_vars[key] = val

        return auto.create_or_select_stack(
            stack_name=stack_name,
            work_dir=self.work_dir,
            opts=auto.LocalWorkspaceOptions(
                env_vars=env_vars,
                secrets_provider=self.secrets_provider,
            ),
        )

    @staticmethod
    def _set_config_value(
        stack: auto.Stack, key: str, value: str, secret: bool = False
    ) -> None:
        """Set a single Pulumi config value on the stack."""
        stack.set_config(key, auto.ConfigValue(value=value, secret=secret))

    def set_all_config(
        self, stack: auto.Stack, config: CustomerConfigResolved
    ) -> None:
        """Set all Pulumi config values from a resolved customer config.

        This replaces the 68 pre-run shell commands that the old
        PulumiDeploymentsClient built.
        """
        _s = self._set_config_value  # shorthand

        # Basic settings
        _s(stack, "customerId", config.customer_id)
        _s(stack, "environment", config.environment)
        _s(stack, "customerRoleArn", config.aws_config.role_arn)
        _s(stack, "externalId", config.aws_config.external_id, secret=True)
        _s(stack, "awsRegion", config.aws_config.region)
        _s(stack, "availabilityZones", ",".join(config.aws_config.availability_zones))

        # VPC
        vpc = config.vpc_config
        _s(stack, "vpcCidr", vpc.cidr_block)
        _s(stack, "natGatewayStrategy", vpc.nat_gateway_strategy.value)

        if vpc.secondary_cidr_blocks:
            _s(stack, "secondaryCidrBlocks", ",".join(vpc.secondary_cidr_blocks))

        _s(stack, "publicSubnets", json.dumps([s.model_dump() for s in vpc.public_subnets]))
        _s(stack, "privateSubnets", json.dumps([s.model_dump() for s in vpc.private_subnets]))
        if vpc.pod_subnets:
            _s(stack, "podSubnets", json.dumps([s.model_dump() for s in vpc.pod_subnets]))

        # VPC Endpoints
        ep = vpc.vpc_endpoints
        _s(stack, "vpcEndpointS3", str(ep.s3).lower())
        _s(stack, "vpcEndpointDynamodb", str(ep.dynamodb).lower())
        _s(stack, "vpcEndpointEcrApi", str(ep.ecr_api).lower())
        _s(stack, "vpcEndpointEcrDkr", str(ep.ecr_dkr).lower())
        _s(stack, "vpcEndpointSts", str(ep.sts).lower())
        _s(stack, "vpcEndpointLogs", str(ep.logs).lower())
        _s(stack, "vpcEndpointEc2", str(ep.ec2).lower())
        _s(stack, "vpcEndpointSsm", str(ep.ssm).lower())
        _s(stack, "vpcEndpointSsmMessages", str(ep.ssmmessages).lower())
        _s(stack, "vpcEndpointEc2Messages", str(ep.ec2messages).lower())
        _s(stack, "vpcEndpointElb", str(ep.elasticloadbalancing).lower())
        _s(stack, "vpcEndpointAutoscaling", str(ep.autoscaling).lower())
        _s(stack, "enableDnsHostnames", str(vpc.enable_dns_hostnames).lower())
        _s(stack, "enableDnsSupport", str(vpc.enable_dns_support).lower())

        # EKS
        eks = config.eks_config
        _s(stack, "eksVersion", eks.version)
        _s(stack, "serviceIpv4Cidr", eks.service_ipv4_cidr)

        access = eks.access
        _s(stack, "endpointPrivateAccess", str(access.endpoint_private_access).lower())
        _s(stack, "endpointPublicAccess", str(access.endpoint_public_access).lower())
        _s(
            stack,
            "bootstrapClusterCreatorAdmin",
            str(access.bootstrap_cluster_creator_admin_permissions).lower(),
        )
        _s(stack, "authenticationMode", access.authentication_mode)
        if access.public_access_cidrs:
            _s(stack, "publicAccessCidrs", ",".join(access.public_access_cidrs))

        # SSM Access Node
        if access.ssm_access_node and access.ssm_access_node.enabled:
            _s(stack, "ssmAccessNodeEnabled", "true")
            _s(stack, "ssmAccessNodeInstanceType", access.ssm_access_node.instance_type)
        else:
            _s(stack, "ssmAccessNodeEnabled", "false")

        # Logging
        _s(stack, "loggingEnabled", str(eks.logging_enabled).lower())
        if eks.logging_enabled and eks.logging_types:
            _s(stack, "loggingTypes", ",".join(eks.logging_types))

        # Encryption
        _s(stack, "encryptionEnabled", str(eks.encryption_enabled).lower())
        if eks.encryption_enabled and eks.encryption_kms_key_arn:
            _s(stack, "encryptionKmsKeyArn", eks.encryption_kms_key_arn)

        _s(stack, "zonalShiftEnabled", str(eks.zonal_shift_enabled).lower())

        # Bootstrap node group
        bng = eks.bootstrap_node_group
        _s(stack, "bootstrapInstanceTypes", ",".join(bng.instance_types))
        _s(stack, "bootstrapDesiredSize", str(bng.desired_size))
        _s(stack, "bootstrapMinSize", str(bng.min_size))
        _s(stack, "bootstrapMaxSize", str(bng.max_size))
        _s(stack, "bootstrapDiskSize", str(bng.disk_size))
        if bng.labels:
            _s(stack, "bootstrapLabels", json.dumps(bng.labels))

        # Karpenter
        karp = eks.karpenter
        _s(stack, "karpenterVersion", karp.version)
        _s(stack, "karpenterInstanceFamilies", ",".join(karp.node_pool.instance_families))
        _s(stack, "karpenterInstanceSizes", ",".join(karp.node_pool.instance_sizes))
        _s(stack, "karpenterCapacityTypes", ",".join(karp.node_pool.capacity_types))
        _s(stack, "karpenterArchitectures", ",".join(karp.node_pool.architectures))
        _s(stack, "karpenterCpuLimit", str(karp.node_pool.cpu_limit))
        _s(stack, "karpenterMemoryLimitGb", str(karp.node_pool.memory_limit_gb))
        _s(stack, "karpenterConsolidationPolicy", karp.disruption.consolidation_policy)
        _s(
            stack,
            "karpenterConsolidateAfterSeconds",
            str(karp.disruption.consolidate_after_seconds),
        )

        # EKS Addons
        addons = eks.addons
        _s(stack, "addonVpcCni", str(addons.vpc_cni.enabled).lower())
        _s(stack, "addonCoredns", str(addons.coredns.enabled).lower())
        _s(stack, "addonKubeProxy", str(addons.kube_proxy.enabled).lower())
        _s(stack, "addonEbsCsi", str(addons.ebs_csi_driver.enabled).lower())
        _s(stack, "addonEfsCsi", str(addons.efs_csi_driver.enabled).lower())
        _s(stack, "addonPodIdentity", str(addons.pod_identity_agent.enabled).lower())
        _s(stack, "addonSnapshot", str(addons.snapshot_controller.enabled).lower())

        # Tags
        if config.tags:
            _s(stack, "tags", json.dumps(config.tags))

        # ArgoCD
        if config.addons and config.addons.argocd and config.addons.argocd.enabled:
            _s(stack, "argoCDEnabled", "true")
        else:
            _s(stack, "argoCDEnabled", "false")

        # Kafka
        if config.kafka_config:
            kafka = config.kafka_config
            _s(stack, "customKafka", str(kafka.custom_kafka).lower())
            _s(stack, "kafkaAuthType", kafka.auth_type.value)
            _s(stack, "kafkaTopic", kafka.topic)
            _s(stack, "kafkaGroupId", kafka.group_id)
            if kafka.bootstrap_servers:
                _s(stack, "kafkaBootstrapServers", kafka.bootstrap_servers)
            if kafka.cluster_arn:
                _s(stack, "kafkaClusterArn", kafka.cluster_arn)
            if kafka.username:
                _s(stack, "kafkaUsername", kafka.username, secret=True)
            if kafka.password:
                _s(stack, "kafkaPassword", kafka.password, secret=True)

        # MongoDB
        if config.mongodb_config:
            mongo = config.mongodb_config
            _s(stack, "mongodbEnabled", "true")
            _s(stack, "mongodbMode", mongo.mode)
            if mongo.atlas_client_id:
                _s(stack, "mongodbAtlasClientId", mongo.atlas_client_id)
            if mongo.atlas_client_secret:
                _s(stack, "mongodbAtlasClientSecret", mongo.atlas_client_secret, secret=True)
            if mongo.atlas_org_id:
                _s(stack, "mongodbAtlasOrgId", mongo.atlas_org_id)
            if mongo.atlas_project_name:
                _s(stack, "mongodbAtlasProjectName", mongo.atlas_project_name)
            if mongo.atlas_project_id:
                _s(stack, "mongodbAtlasProjectId", mongo.atlas_project_id)
            if mongo.atlas_cluster_name:
                _s(stack, "mongodbAtlasClusterName", mongo.atlas_cluster_name)
            _s(stack, "mongodbClusterTier", mongo.cluster_tier)
            _s(stack, "mongodbClusterRegion", mongo.cluster_region)
            _s(stack, "mongodbDbUsername", mongo.db_username)
            if mongo.db_password:
                _s(stack, "mongodbDbPassword", mongo.db_password, secret=True)
            _s(stack, "mongodbDiskSizeGb", str(mongo.disk_size_gb))
            _s(stack, "mongodbAtlasCidrBlock", mongo.atlas_cidr_block)
            if mongo.connection_uri:
                _s(stack, "mongodbConnectionUri", mongo.connection_uri, secret=True)

        # ESO Secrets — platform defaults from settings, customer provides only API keys
        from api.settings import settings as platform_settings

        eso = config.eso_secrets
        _s(stack, "esoFalkordbPassword", platform_settings.falkordb_password, secret=True)
        _s(stack, "esoMilvusToken", platform_settings.milvus_token, secret=True)
        _s(stack, "esoGoogleApiKey", platform_settings.google_api_key)
        _s(stack, "esoGeminiApiKey", platform_settings.gemini_api_key)
        _s(
            stack,
            "esoGithubArgocdCdToken",
            platform_settings.github_pat,
            secret=True,
        )

        # NextJS secrets — all from platform settings
        ps = platform_settings
        if ps.nextjs_nextauth_secret:
            _s(stack, "nextjsNextauthSecret", ps.nextjs_nextauth_secret, secret=True)
        if ps.nextjs_google_client_id:
            _s(stack, "nextjsGoogleClientId", ps.nextjs_google_client_id)
        if ps.nextjs_google_client_secret:
            _s(
                stack,
                "nextjsGoogleClientSecret",
                ps.nextjs_google_client_secret,
                secret=True,
            )
        if ps.nextjs_auth_dynamodb_id:
            _s(stack, "nextjsAuthDynamodbId", ps.nextjs_auth_dynamodb_id, secret=True)
        if ps.nextjs_auth_dynamodb_secret:
            _s(
                stack,
                "nextjsAuthDynamodbSecret",
                ps.nextjs_auth_dynamodb_secret,
                secret=True,
            )
        if ps.nextjs_aws_config:
            _s(stack, "nextjsAwsConfig", ps.nextjs_aws_config, secret=True)
        if ps.nextjs_mcp_encryption_key:
            _s(
                stack,
                "nextjsMcpEncryptionKey",
                ps.nextjs_mcp_encryption_key,
                secret=True,
            )
        if ps.nextjs_resend_api_key:
            _s(stack, "nextjsResendApiKey", ps.nextjs_resend_api_key, secret=True)
        if ps.nextjs_stripe_secret_key:
            _s(stack, "nextjsStripeSecretKey", ps.nextjs_stripe_secret_key, secret=True)

    def deploy(
        self,
        stack_name: str,
        config: CustomerConfigResolved,
        on_output: Any = None,
    ) -> auto.UpResult:
        """Run pulumi up for a customer stack."""
        stack = self._get_stack(stack_name)
        self.set_all_config(stack, config)

        logger.info("Starting pulumi up for stack %s", stack_name)
        result = stack.up(on_output=on_output or (lambda msg: logger.info(msg)))
        logger.info(
            "Pulumi up completed for %s: %s (%d resources)",
            stack_name,
            result.summary.result,
            result.summary.resource_changes.get("create", 0)
            + result.summary.resource_changes.get("same", 0)
            + result.summary.resource_changes.get("update", 0),
        )
        return result

    def destroy(
        self,
        stack_name: str,
        on_output: Any = None,
    ) -> auto.DestroyResult:
        """Run pulumi destroy for a customer stack."""
        stack = self._get_stack(stack_name)

        logger.info("Starting pulumi destroy for stack %s", stack_name)
        result = stack.destroy(on_output=on_output or (lambda msg: logger.info(msg)))
        logger.info("Pulumi destroy completed for %s: %s", stack_name, result.summary.result)
        return result

    def get_outputs(self, stack_name: str) -> dict[str, Any]:
        """Get stack outputs as a plain dict."""
        stack = self._get_stack(stack_name)
        outputs = stack.outputs()
        return {k: v.value for k, v in outputs.items()}

    def refresh(self, stack_name: str) -> auto.RefreshResult:
        """Run pulumi refresh for a customer stack."""
        stack = self._get_stack(stack_name)
        return stack.refresh(on_output=lambda msg: logger.info(msg))
