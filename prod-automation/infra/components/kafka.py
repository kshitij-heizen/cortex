import pulumi
import pulumi_aws as aws


class KafkaCluster(pulumi.ComponentResource):
    """MSK Serverless cluster for Pulumi-managed Kafka.

    Creates an MSK Serverless cluster with SASL/IAM authentication
    in the customer's VPC private subnets.
    """

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Output[str],
        private_subnet_ids: pulumi.Output[list[str]],
        cluster_security_group_id: pulumi.Output[str],
        region: str = "us-east-1",
        provider: aws.Provider | None = None,
        tags: dict[str, str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("byoc:infrastructure:KafkaCluster", name, None, opts)
        self._tags = tags or {}
        child_opts = pulumi.ResourceOptions(parent=self, provider=provider)

        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-msk-sg",
            vpc_id=vpc_id,
            description="MSK Serverless security group",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=9098,
                    to_port=9098,
                    security_groups=[cluster_security_group_id],
                    description="SASL/IAM from EKS pods",
                ),
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="Allow all outbound",
                ),
            ],
            tags={"Name": f"{name}-msk-sg", **self._tags},
            opts=child_opts,
        )

        self.cluster = aws.msk.ServerlessCluster(
            f"{name}-msk-serverless",
            cluster_name=f"{name}-msk",
            client_authentication=aws.msk.ServerlessClusterClientAuthenticationArgs(
                sasl=aws.msk.ServerlessClusterClientAuthenticationSaslArgs(
                    iam=aws.msk.ServerlessClusterClientAuthenticationSaslIamArgs(
                        enabled=True,
                    ),
                ),
            ),
            vpc_configs=[
                aws.msk.ServerlessClusterVpcConfigArgs(
                    subnet_ids=private_subnet_ids,
                    security_group_ids=[self.security_group.id],
                ),
            ],
            tags={"Name": f"{name}-msk", **self._tags},
            opts=child_opts,
        )

        self.cluster_arn = self.cluster.arn

        self.register_outputs(
            {
                "cluster_arn": self.cluster_arn,
                "security_group_id": self.security_group.id,
            }
        )
