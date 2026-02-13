import json

import pulumi
import pulumi_aws as aws


class EksIamRoles(pulumi.ComponentResource):
    """IAM roles required for EKS cluster, worker nodes, and Karpenter."""

    def __init__(
        self,
        name: str,
        oidc_provider_arn: pulumi.Output[str] | None = None,
        oidc_provider_url: pulumi.Output[str] | None = None,
        provider: aws.Provider | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("byoc:infrastructure:EksIamRoles", name, None, opts)

        child_opts = pulumi.ResourceOptions(parent=self, provider=provider)
        self._name = name
        self._provider = provider

        eks_assume_role_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "eks.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )

        self.cluster_role = aws.iam.Role(
            f"{name}-eks-cluster-role",
            assume_role_policy=eks_assume_role_policy,
            opts=child_opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-eks-cluster-policy",
            role=self.cluster_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
            opts=child_opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-eks-vpc-resource-controller",
            role=self.cluster_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonEKSVPCResourceController",
            opts=child_opts,
        )

        ec2_assume_role_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )

        self.node_role = aws.iam.Role(
            f"{name}-eks-node-role",
            assume_role_policy=ec2_assume_role_policy,
            opts=child_opts,
        )

        node_policies = [
            "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
            "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        ]

        for i, policy_arn in enumerate(node_policies):
            aws.iam.RolePolicyAttachment(
                f"{name}-eks-node-policy-{i}",
                role=self.node_role.name,
                policy_arn=policy_arn,
                opts=child_opts,
            )

        self.node_instance_profile = aws.iam.InstanceProfile(
            f"{name}-eks-node-instance-profile",
            role=self.node_role.name,
            opts=child_opts,
        )

        # Store OIDC info for later Karpenter role creation
        self._oidc_provider_arn = oidc_provider_arn
        self._oidc_provider_url = oidc_provider_url

        # Export basic outputs
        self.cluster_role_arn = self.cluster_role.arn
        self.node_role_arn = self.node_role.arn
        self.node_role_name = self.node_role.name
        self.node_instance_profile_arn = self.node_instance_profile.arn
        self.node_instance_profile_name = self.node_instance_profile.name

        self.register_outputs(
            {
                "cluster_role_arn": self.cluster_role_arn,
                "node_role_arn": self.node_role_arn,
                "node_role_name": self.node_role_name,
                "node_instance_profile_arn": self.node_instance_profile_arn,
            }
        )

    def create_karpenter_controller_role(
        self,
        oidc_provider_arn: pulumi.Output[str],
        oidc_provider_url: pulumi.Output[str],
        cluster_name: pulumi.Output[str],
    ) -> aws.iam.Role:
        """Create IAM role for Karpenter controller using IRSA.

        This must be called after the EKS cluster is created since it needs
        the OIDC provider ARN and URL. cluster_name must be the actual EKS
        cluster name (e.g. eks.cluster.name) for DescribeCluster permission.
        """
        child_opts = pulumi.ResourceOptions(parent=self, provider=self._provider)
        discovery_tag = f"{self._name}-eks-cluster"  # For EC2/iam tag conditions; DescribeCluster uses cluster_name

        # Karpenter controller assume role policy (IRSA)
        assume_role_policy = pulumi.Output.all(
            oidc_provider_arn, oidc_provider_url
        ).apply(
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
                                    f"{args[1].replace('https://', '')}:aud": "sts.amazonaws.com",
                                    f"{args[1].replace('https://', '')}:sub": "system:serviceaccount:karpenter:karpenter",
                                }
                            },
                        }
                    ],
                }
            )
        )

        self.karpenter_controller_role = aws.iam.Role(
            f"{self._name}-karpenter-controller-role",
            assume_role_policy=assume_role_policy,
            opts=child_opts,
        )

        # Karpenter controller policy (use actual cluster name for DescribeCluster)
        karpenter_policy_document = pulumi.Output.all(
            self.node_role.arn,
            self.node_instance_profile.arn,
            cluster_name,
        ).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowScopedEC2InstanceAccessActions",
                            "Effect": "Allow",
                            "Action": [
                                "ec2:RunInstances",
                                "ec2:CreateFleet",
                            ],
                            "Resource": [
                                "arn:aws:ec2:*::image/*",
                                "arn:aws:ec2:*::snapshot/*",
                                "arn:aws:ec2:*:*:security-group/*",
                                "arn:aws:ec2:*:*:subnet/*",
                            ],
                        },
                        {
                            "Sid": "AllowScopedEC2LaunchTemplateAccessActions",
                            "Effect": "Allow",
                            "Action": [
                                "ec2:RunInstances",
                                "ec2:CreateFleet",
                            ],
                            "Resource": "arn:aws:ec2:*:*:launch-template/*",
                            "Condition": {
                                "StringEquals": {
                                    f"aws:ResourceTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedEC2InstanceActionsWithTags",
                            "Effect": "Allow",
                            "Action": [
                                "ec2:RunInstances",
                                "ec2:CreateFleet",
                                "ec2:CreateLaunchTemplate",
                            ],
                            "Resource": [
                                "arn:aws:ec2:*:*:fleet/*",
                                "arn:aws:ec2:*:*:instance/*",
                                "arn:aws:ec2:*:*:volume/*",
                                "arn:aws:ec2:*:*:network-interface/*",
                                "arn:aws:ec2:*:*:launch-template/*",
                                "arn:aws:ec2:*:*:spot-instances-request/*",
                            ],
                            "Condition": {
                                "StringEquals": {
                                    f"aws:RequestTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedResourceCreationTagging",
                            "Effect": "Allow",
                            "Action": "ec2:CreateTags",
                            "Resource": [
                                "arn:aws:ec2:*:*:fleet/*",
                                "arn:aws:ec2:*:*:instance/*",
                                "arn:aws:ec2:*:*:volume/*",
                                "arn:aws:ec2:*:*:network-interface/*",
                                "arn:aws:ec2:*:*:launch-template/*",
                                "arn:aws:ec2:*:*:spot-instances-request/*",
                            ],
                            "Condition": {
                                "StringEquals": {
                                    f"aws:RequestTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedResourceTagging",
                            "Effect": "Allow",
                            "Action": "ec2:CreateTags",
                            "Resource": "arn:aws:ec2:*:*:instance/*",
                            "Condition": {
                                "StringEquals": {
                                    f"aws:ResourceTag/karpenter.sh/discovery": discovery_tag
                                },
                                "ForAllValues:StringEquals": {
                                    "aws:TagKeys": ["karpenter.sh/nodeclaim", "Name"]
                                },
                            },
                        },
                        {
                            "Sid": "AllowScopedDeletion",
                            "Effect": "Allow",
                            "Action": [
                                "ec2:TerminateInstances",
                                "ec2:DeleteLaunchTemplate",
                            ],
                            "Resource": [
                                "arn:aws:ec2:*:*:instance/*",
                                "arn:aws:ec2:*:*:launch-template/*",
                            ],
                            "Condition": {
                                "StringEquals": {
                                    f"aws:ResourceTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowRegionalReadActions",
                            "Effect": "Allow",
                            "Action": [
                                "ec2:DescribeAvailabilityZones",
                                "ec2:DescribeImages",
                                "ec2:DescribeInstances",
                                "ec2:DescribeInstanceTypeOfferings",
                                "ec2:DescribeInstanceTypes",
                                "ec2:DescribeLaunchTemplates",
                                "ec2:DescribeSecurityGroups",
                                "ec2:DescribeSpotPriceHistory",
                                "ec2:DescribeSubnets",
                            ],
                            "Resource": "*",
                        },
                        {
                            "Sid": "AllowSSMReadActions",
                            "Effect": "Allow",
                            "Action": "ssm:GetParameter",
                            "Resource": "arn:aws:ssm:*:*:parameter/aws/service/*",
                        },
                        {
                            "Sid": "AllowPricing",
                            "Effect": "Allow",
                            "Action": "pricing:GetProducts",
                            "Resource": "*",
                        },
                        {
                            "Sid": "AllowPassingInstanceRole",
                            "Effect": "Allow",
                            "Action": "iam:PassRole",
                            "Resource": args[0],
                            "Condition": {
                                "StringEquals": {
                                    "iam:PassedToService": "ec2.amazonaws.com"
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedInstanceProfileCreationActions",
                            "Effect": "Allow",
                            "Action": "iam:CreateInstanceProfile",
                            "Resource": "*",
                            "Condition": {
                                "StringEquals": {
                                    f"aws:RequestTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedInstanceProfileTagActions",
                            "Effect": "Allow",
                            "Action": "iam:TagInstanceProfile",
                            "Resource": "*",
                            "Condition": {
                                "StringEquals": {
                                    f"aws:ResourceTag/karpenter.sh/discovery": discovery_tag,
                                    f"aws:RequestTag/karpenter.sh/discovery": discovery_tag,
                                }
                            },
                        },
                        {
                            "Sid": "AllowScopedInstanceProfileActions",
                            "Effect": "Allow",
                            "Action": [
                                "iam:AddRoleToInstanceProfile",
                                "iam:RemoveRoleFromInstanceProfile",
                                "iam:DeleteInstanceProfile",
                            ],
                            "Resource": "*",
                            "Condition": {
                                "StringEquals": {
                                    f"aws:ResourceTag/karpenter.sh/discovery": discovery_tag
                                }
                            },
                        },
                        {
                            "Sid": "AllowInstanceProfileReadActions",
                            "Effect": "Allow",
                            "Action": "iam:GetInstanceProfile",
                            "Resource": "*",
                        },
                        {
                            "Sid": "AllowAPIServerEndpointDiscovery",
                            "Effect": "Allow",
                            "Action": "eks:DescribeCluster",
                            "Resource": f"arn:aws:eks:*:*:cluster/{args[2]}",
                        },
                    ],
                }
            )
        )

        self.karpenter_controller_policy = aws.iam.Policy(
            f"{self._name}-karpenter-controller-policy",
            policy=karpenter_policy_document,
            opts=child_opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{self._name}-karpenter-controller-policy-attachment",
            role=self.karpenter_controller_role.name,
            policy_arn=self.karpenter_controller_policy.arn,
            opts=child_opts,
        )

        self.karpenter_controller_role_arn = self.karpenter_controller_role.arn

        return self.karpenter_controller_role