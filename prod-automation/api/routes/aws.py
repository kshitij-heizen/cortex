"""AWS connection testing endpoints."""

import logging

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth_models import UserResponse
from api.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/aws",
    tags=["aws"],
    dependencies=[Depends(get_current_user)],
)


class TestConnectionRequest(BaseModel):
    role_arn: str = Field(
        ...,
        description="IAM role ARN to assume",
        pattern=r"^arn:aws:iam::\d{12}:role/.+$",
    )
    external_id: str = Field(
        ...,
        description="External ID for secure role assumption",
        min_length=10,
    )
    region: str = Field(default="us-east-1", description="AWS region")


class TestConnectionSuccess(BaseModel):
    status: str = "connected"
    account_id: str
    assumed_role_arn: str
    region: str


class TestConnectionFailure(BaseModel):
    status: str = "failed"
    error: str


@router.post(
    "/test-connection",
    response_model=TestConnectionSuccess,
    responses={403: {"model": TestConnectionFailure}},
    summary="Test AWS cross-account role assumption",
)
async def test_connection(
    request: TestConnectionRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Test AWS connection by attempting to assume the provided IAM role.
    Uses STS AssumeRole and then calls GetCallerIdentity to verify access.
    """
    try:
        sts = boto3.client("sts", region_name=request.region)

        assumed = sts.assume_role(
            RoleArn=request.role_arn,
            ExternalId=request.external_id,
            RoleSessionName="hydradb-connection-test",
            DurationSeconds=900,
        )

        creds = assumed["Credentials"]

        # Verify by calling GetCallerIdentity with assumed credentials
        assumed_sts = boto3.client(
            "sts",
            region_name=request.region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )

        identity = assumed_sts.get_caller_identity()

        return TestConnectionSuccess(
            status="connected",
            account_id=identity["Account"],
            assumed_role_arn=identity["Arn"],
            region=request.region,
        )

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        logger.warning(
            "AWS connection test failed for role %s: %s - %s",
            request.role_arn,
            error_code,
            error_msg,
        )

        if error_code == "AccessDenied":
            detail = "Access denied — check trust policy and external ID"
        elif error_code == "MalformedPolicyDocument":
            detail = "Malformed trust policy on the IAM role"
        elif error_code == "RegionDisabledException":
            detail = f"Region {request.region} is not enabled in the target account"
        else:
            detail = f"{error_code}: {error_msg}"

        raise HTTPException(status_code=403, detail=detail)

    except NoCredentialsError:
        logger.error("Platform AWS credentials not configured")
        raise HTTPException(
            status_code=500,
            detail="Platform AWS credentials are not configured. Contact support.",
        )

    except Exception as e:
        logger.exception("Unexpected error during AWS connection test")
        raise HTTPException(status_code=500, detail=str(e))
