# BYOC Platform — EC2 Deployment Guide

Manual steps to deploy the platform on a single EC2 instance with Docker Compose.

## Prerequisites

- AWS CLI configured with admin-level access to account `225919348997`
- A MongoDB Atlas cluster with a connection URI ready
- A domain pointed to the Elastic IP (e.g., `platform.usecortex.opengig.work`)

## 1. Create IAM Role for EC2

The platform EC2 needs permissions for: STS (assume customer roles), S3 (Pulumi state), KMS (Pulumi secrets), SSM (addon
install), CloudWatch Logs, Secrets Manager.

```bash
cat > /tmp/ec2-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name byoc-platform-ec2 \
  --assume-role-policy-document file:///tmp/ec2-trust-policy.json \
  --description "BYOC platform EC2 instance role"

cat > /tmp/byoc-platform-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "STSAssumeCustomerRoles",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/*"
    },
    {
      "Sid": "PulumiStateS3",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
        "s3:ListBucket", "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::cortex-pulumi-state",
        "arn:aws:s3:::cortex-pulumi-state/*"
      ]
    },
    {
      "Sid": "PulumiSecretsKMS",
      "Effect": "Allow",
      "Action": [
        "kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey",
        "kms:DescribeKey"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:RequestAlias": "alias/pulumi-secrets"
        }
      }
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup", "logs:CreateLogStream",
        "logs:PutLogEvents", "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:*:225919348997:log-group:/byoc/*"
    },
    {
      "Sid": "SecretsManager",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:*:225919348997:secret:/byoc/*"
    },
    {
      "Sid": "SSMSendCommand",
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand", "ssm:GetCommandInvocation",
        "ssm:PutParameter", "ssm:GetParameter"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2Describe",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances", "ec2:DescribeVpcEndpoints"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name byoc-platform-ec2 \
  --policy-name byoc-platform-permissions \
  --policy-document file:///tmp/byoc-platform-policy.json

aws iam create-instance-profile --instance-profile-name byoc-platform-ec2
aws iam add-role-to-instance-profile \
  --instance-profile-name byoc-platform-ec2 \
  --role-name byoc-platform-ec2
```

## 2. Create Security Group

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)

aws ec2 create-security-group \
  --group-name byoc-platform-sg \
  --description "BYOC platform - HTTP/HTTPS/SSH" \
  --vpc-id "$VPC_ID"

SG_ID=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values=byoc-platform-sg \
  --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
```

## 3. Allocate Elastic IP

```bash
aws ec2 allocate-address --domain vpc --tag-specifications \
  'ResourceType=elastic-ip,Tags=[{Key=Name,Value=byoc-platform}]'
```

cat > /tmp/user-data.sh << 'EOF' #!/bin/bash set -e

# Update system

yum update -y

# Install Docker

yum install -y docker git systemctl enable docker systemctl start docker

# Install Docker Compose v2

DOCKER_CONFIG=/usr/local/lib/docker/cli-plugins mkdir -p
"$DOCKER_CONFIG"
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
 -o "$DOCKER_CONFIG/docker-compose"
chmod +x "$DOCKER_CONFIG/docker-compose"

# Add ec2-user to docker group

usermod -aG docker ec2-user

# Install certbot

yum install -y certbot

echo "=== Docker + Compose installed ===" EOF

## 4. Launch EC2 Instance

```bash
aws ec2 run-instances \
  --image-id ami-0c02fb55956c7d316 \
  --instance-type t3.medium \
  --key-name cortexkshitij \
  --security-group-ids "$SG_ID" \
  --iam-instance-profile Name=byoc-platform-ec2 \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=byoc-platform}]' \
  --user-data file:///tmp/user-data.sh \
  --count 1
```

## 5. Deploy the Application

```bash
git clone https://github.com/kshitij-heizen/cortex.git
cd cortex/prod-automation
cp .env.example .env
mkdir -p nginx/certs
docker compose build
docker compose up -d
curl http://localhost/health
```
