import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth_models import UserResponse
from api.config_storage import config_storage
from api.database import db
from api.dependencies import get_current_user
from api.models import (
    CustomerDeployment,
    DeploymentResponse,
    DeploymentStatus,
    DeployRequest,
    DestroyRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/deployments",
    tags=["deployments"],
)


def _parse_deployment_outputs(outputs_raw: str | None) -> dict | None:
    """Safely parse deployment outputs JSON."""
    if not outputs_raw or not outputs_raw.strip():
        return None
    try:
        return json.loads(outputs_raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _doc_to_deployment(d: dict[str, Any]) -> CustomerDeployment:
    """Convert a MongoDB deployment document to a CustomerDeployment model."""
    return CustomerDeployment(
        id=str(d.get("_id", "")),
        customer_id=d["customer_id"],
        environment=d["environment"],
        stack_name=d["stack_name"],
        aws_region=d["aws_region"],
        role_arn=d["role_arn"],
        status=d["status"],
        pulumi_deployment_id=d.get("pulumi_deployment_id"),
        outputs=_parse_deployment_outputs(d.get("outputs")),
        error_message=d.get("error_message"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


@router.post(
    "/{customer_id}",
    response_model=DeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Deploy customer infrastructure",
)
async def deploy(
    customer_id: str,
    request: DeployRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> DeploymentResponse:
    """Deploy infrastructure for a customer.

    Dispatches a Celery task to run Pulumi deploy, GitOps write, and addon install.
    """
    config = config_storage.get(current_user.id, customer_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration for customer '{customer_id}' not found. "
            "Create a configuration first using POST /api/v1/configs",
        )

    stack_name = f"{customer_id}-{request.environment}"

    existing = db.get_deployment_for_user(current_user.id, customer_id, request.environment)
    if existing:
        if existing["status"] == DeploymentStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment {stack_name} is already in progress",
            )
        if existing["status"] == DeploymentStatus.DESTROYING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment {stack_name} is being destroyed. "
                "Wait for destruction to complete.",
            )

    if existing is None:
        try:
            db.create_deployment(
                user_id=current_user.id,
                customer_id=customer_id,
                environment=request.environment,
                aws_region=config.aws_config.region,
                role_arn=config.aws_config.role_arn,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    else:
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.PENDING,
        )

    from worker.celery_app import deploy_task

    task = deploy_task.delay(customer_id, request.environment)

    db.audit_log(
        "deployment_started",
        customer_id,
        user_id=current_user.id,
        environment=request.environment,
        actor=current_user.email,
    )

    return DeploymentResponse(
        customer_id=customer_id,
        environment=request.environment,
        stack_name=stack_name,
        status=DeploymentStatus.PENDING,
        message=f"Deployment queued (task_id={task.id}). Check status endpoint for progress.",
    )


@router.get(
    "/{customer_id}/{environment}/status",
    response_model=CustomerDeployment,
    summary="Get deployment status",
)
async def get_deployment_status(
    customer_id: str,
    environment: str = "prod",
    current_user: UserResponse = Depends(get_current_user),
) -> CustomerDeployment:
    """Get the current deployment status."""
    deployment = db.get_deployment_for_user(current_user.id, customer_id, environment)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment for {customer_id}-{environment} not found",
        )

    return _doc_to_deployment(deployment)


@router.get(
    "/{customer_id}",
    response_model=list[CustomerDeployment],
    summary="List customer deployments",
)
async def list_customer_deployments(
    customer_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> list[CustomerDeployment]:
    """List all deployments for a customer owned by the current user."""
    # Verify the user owns this customer config
    config = config_storage.get(current_user.id, customer_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' not found",
        )
    deployments = db.get_deployments_by_customer(customer_id)
    # Filter to only this user's deployments
    user_deployments = [d for d in deployments if d.get("user_id") == current_user.id]
    return [_doc_to_deployment(d) for d in user_deployments]


@router.post(
    "/{customer_id}/{environment}/destroy",
    response_model=DeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Destroy customer infrastructure",
)
async def destroy(
    customer_id: str,
    environment: str,
    request: DestroyRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> DeploymentResponse:
    """Destroy infrastructure for a customer."""
    stack_name = f"{customer_id}-{environment}"

    existing = db.get_deployment_for_user(current_user.id, customer_id, environment)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment {stack_name} not found",
        )

    if existing["status"] == DeploymentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} is in progress. "
            "Wait for it to complete before destroying.",
        )
    if existing["status"] == DeploymentStatus.DESTROYING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} is already being destroyed",
        )
    if existing["status"] == DeploymentStatus.DESTROYED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} has already been destroyed",
        )

    from worker.celery_app import destroy_task

    task = destroy_task.delay(customer_id, environment)

    db.audit_log(
        "deployment_destroy_started",
        customer_id,
        user_id=current_user.id,
        environment=environment,
        actor=current_user.email,
    )

    return DeploymentResponse(
        customer_id=customer_id,
        environment=environment,
        stack_name=stack_name,
        status=DeploymentStatus.DESTROYING,
        message=f"Destruction queued (task_id={task.id}). Check status endpoint for progress. "
        "This operation cannot be undone.",
    )


@router.post(
    "/{customer_id}/{environment}/addons/install",
    response_model=DeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Install addons on a deployed cluster",
)
async def install_addons(
    customer_id: str,
    environment: str,
    current_user: UserResponse = Depends(get_current_user),
) -> DeploymentResponse:
    """Trigger addon installation (Karpenter + ArgoCD) via Celery task."""
    stack_name = f"{customer_id}-{environment}"

    existing = db.get_deployment_for_user(current_user.id, customer_id, environment)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment {stack_name} not found",
        )

    if existing["status"] != DeploymentStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} must be in SUCCEEDED state to install addons. "
            f"Current status: {existing['status'].value}",
        )

    from worker.celery_app import install_addons_task

    task = install_addons_task.delay(customer_id, environment)

    return DeploymentResponse(
        customer_id=customer_id,
        environment=environment,
        stack_name=stack_name,
        status=existing["status"],
        message=f"Addon install queued (task_id={task.id}). "
        "Check cluster access endpoint for SSM command status.",
    )
