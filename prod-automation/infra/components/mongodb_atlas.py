"""MongoDB Atlas cluster provisioning and VPC peering."""

import pulumi
import pulumi_mongodbatlas as atlas
import pulumi_aws as aws
from dataclasses import dataclass
from typing import Optional


@dataclass
class MongoAtlasResult:
    """Result of MongoDB Atlas provisioning."""
    connection_string: pulumi.Output[str]
    project_id: pulumi.Output[str]


def provision_atlas_cluster(
    customer_id: str,
    mongo_config,
    vpc_id: pulumi.Input[str],
    vpc_cidr: str,
    route_table_ids: list[pulumi.Input[str]],
    node_security_group_id: pulumi.Input[str],
    aws_account_id: str,
    aws_region: str,
    aws_provider: aws.Provider,
) -> MongoAtlasResult:
    """Provision MongoDB Atlas cluster with VPC peering.

    Supports two modes:
    - 'atlas': Create new project + cluster + peering
    - 'atlas-peering': Peer to existing project/cluster
    """

    # Configure Atlas provider
    atlas_provider = atlas.Provider(
        f"{customer_id}-atlas-provider",
        public_key=mongo_config.atlas_public_key,
        private_key=mongo_config.atlas_private_key,
    )

    opts = pulumi.ResourceOptions(provider=atlas_provider)

    if mongo_config.mode == "atlas":
        # Create new Atlas project
        project = atlas.Project(
            f"{customer_id}-atlas-project",
            name=mongo_config.atlas_project_name or f"{customer_id}-cortex",
            org_id=mongo_config.atlas_org_id,
            opts=opts,
        )
        project_id = project.id

        # Create Atlas cluster
        cluster = atlas.Cluster(
            f"{customer_id}-atlas-cluster",
            project_id=project_id,
            name=f"{customer_id}-cortex",
            provider_name="AWS",
            provider_instance_size_name=mongo_config.cluster_tier,
            provider_region_name=mongo_config.cluster_region,
            disk_size_gb=mongo_config.disk_size_gb,
            cluster_type="REPLICASET",
            opts=opts,
        )

        # Create database user
        db_user = atlas.DatabaseUser(
            f"{customer_id}-atlas-db-user",
            project_id=project_id,
            username=mongo_config.db_username,
            password=mongo_config.db_password,
            auth_database_name="admin",
            roles=[
                atlas.DatabaseUserRoleArgs(
                    role_name="readWriteAnyDatabase",
                    database_name="admin",
                ),
            ],
            opts=opts,
        )

        connection_string = cluster.connection_strings.apply(
            lambda cs: cs[0].standard_srv if cs else ""
        )

    else:
        # atlas-peering mode: use existing project/cluster
        project_id = mongo_config.atlas_project_id

        # Look up existing cluster connection string
        existing_cluster = atlas.get_cluster(
            project_id=mongo_config.atlas_project_id,
            name=mongo_config.atlas_cluster_name,
        )
        connection_string = pulumi.Output.from_input(
            existing_cluster.connection_strings[0].private_srv
            or existing_cluster.connection_strings[0].standard_srv
        )

    # --- VPC Peering ---

    # Create peering from Atlas side
    peering = atlas.NetworkPeering(
        f"{customer_id}-atlas-vpc-peering",
        project_id=project_id,
        container_id=atlas.get_network_container_output(
            project_id=project_id,
            container_id="",  # Will be auto-resolved
        ).id if mongo_config.mode == "atlas" else "",
        provider_name="AWS",
        accepter_region_name=aws_region.replace("-", "_").upper(),
        aws_account_id=aws_account_id,
        vpc_id=vpc_id,
        route_table_cidr_block=vpc_cidr,
        opts=opts,
    )

    # Accept peering on AWS side
    peering_accepter = aws.ec2.VpcPeeringConnectionAccepter(
        f"{customer_id}-atlas-peering-accepter",
        vpc_peering_connection_id=peering.connection_id,
        auto_accept=True,
        opts=pulumi.ResourceOptions(provider=aws_provider),
    )

    # Get Atlas CIDR from the peering
    atlas_cidr = peering.atlas_cidr_block

    # Add routes to Atlas CIDR in each route table
    for i, rt_id in enumerate(route_table_ids):
        aws.ec2.Route(
            f"{customer_id}-atlas-route-{i}",
            route_table_id=rt_id,
            destination_cidr_block=atlas_cidr,
            vpc_peering_connection_id=peering.connection_id,
            opts=pulumi.ResourceOptions(
                provider=aws_provider,
                depends_on=[peering_accepter],
            ),
        )

    # Allow MongoDB traffic from Atlas CIDR
    aws.ec2.SecurityGroupRule(
        f"{customer_id}-atlas-sg-rule",
        type="ingress",
        from_port=27017,
        to_port=27017,
        protocol="tcp",
        cidr_blocks=[atlas_cidr],
        security_group_id=node_security_group_id,
        description="Allow MongoDB from Atlas VPC",
        opts=pulumi.ResourceOptions(
            provider=aws_provider,
            depends_on=[peering_accepter],
        ),
    )

    # Whitelist our VPC CIDR in Atlas
    atlas.ProjectIpAccessList(
        f"{customer_id}-atlas-ip-access",
        project_id=project_id,
        cidr_block=vpc_cidr,
        comment=f"BYOC VPC {customer_id}",
        opts=opts,
    )

    # Build connection URI with credentials
    if mongo_config.mode == "atlas":
        full_uri = connection_string.apply(
            lambda cs: cs.replace(
                "mongodb+srv://",
                f"mongodb+srv://{mongo_config.db_username}:{mongo_config.db_password}@",
            ) + "/admin?retryWrites=true&w=majority"
            if cs else ""
        )
    else:
        full_uri = connection_string

    return MongoAtlasResult(
        connection_string=full_uri,
        project_id=pulumi.Output.from_input(project_id),
    )
