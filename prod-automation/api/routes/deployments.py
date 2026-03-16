"""Deployment management endpoints — Automation API (S3 backend)."""

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from api.config_storage import config_storage
from api.database import Database, db
from api.models import (
    CustomerConfigResolved,
    CustomerDeployment,
    DeploymentResponse,
    DeploymentStatus,
    DeployRequest,
    DestroyRequest,
)
from api.pulumi_engine import PulumiEngine
from api.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])

# Delay before auto-installing addons so access node user-data can finish
AUTO_INSTALL_ADDONS_DELAY_SECONDS = 90


def _parse_deployment_outputs(outputs_raw: str | None) -> dict | None:
    """Safely parse deployment outputs JSON."""
    if not outputs_raw or not outputs_raw.strip():
        return None
    try:
        return json.loads(outputs_raw)
    except (json.JSONDecodeError, TypeError):
        return None


def get_pulumi_engine() -> PulumiEngine:
    """Get a PulumiEngine instance configured from settings."""
    return PulumiEngine(
        backend_url=settings.pulumi_backend_url,
        secrets_provider=settings.pulumi_secrets_provider,
        work_dir=settings.pulumi_work_dir,
    )


async def _auto_install_addons(customer_id: str, environment: str) -> None:
    """Auto-trigger addon installation after deployment succeeds."""
    try:
        config = config_storage.get(customer_id)
        if not config or not config.addons:
            return

        argocd = config.addons.argocd
        if not argocd or not argocd.enabled:
            return

        logger.info("Auto-installing addons for %s-%s", customer_id, environment)
        installer = AddonInstallerService(customer_id, environment)
        result = await installer.install_all_addons()
        logger.info(
            "Addon install triggered for %s-%s: command_id=%s",
            customer_id,
            environment,
            result.ssm_command_id,
        )
    except Exception:
        logger.exception(
            "Auto-install addons failed for %s-%s (can be retried manually)",
            customer_id,
            environment,
        )


async def run_deployment(
    config: CustomerConfigResolved,
    environment: str,
    database: Database,
) -> None:
    """Background task to run customer deployment via Automation API."""
    stack_name = f"{config.customer_id}-{environment}"

    try:
        engine = get_pulumi_engine()

        database.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.IN_PROGRESS,
        )

        # Run pulumi up in a thread (it's blocking)
        result = await asyncio.to_thread(engine.deploy, stack_name, config)

        if result.summary.result == "succeeded":
            # Get outputs and store them
            outputs = await asyncio.to_thread(engine.get_outputs, stack_name)
            database.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.SUCCEEDED,
                outputs=json.dumps(outputs),
                error_message="",
            )

            # Write GitOps values and applications to Git
            from api.services.gitops_writer import GitOpsWriter

            try:
                writer = GitOpsWriter(config, outputs)
                await asyncio.to_thread(writer.push_to_github)
                logger.info("GitOps values pushed for %s", stack_name)
            except Exception:
                logger.exception("GitOps write failed for %s", stack_name)

            # Auto-install addons after delay
            logger.info(
                "Waiting %ds for access node user-data before addon install",
                AUTO_INSTALL_ADDONS_DELAY_SECONDS,
            )
            await asyncio.sleep(AUTO_INSTALL_ADDONS_DELAY_SECONDS)
            await _auto_install_addons(config.customer_id, environment)
        else:
            database.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.FAILED,
                error_message=f"Pulumi up finished with result: {result.summary.result}",
            )

    except Exception as e:
        logger.exception("Deployment failed for %s", stack_name)
        database.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=str(e),
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
    background_tasks: BackgroundTasks,
) -> DeploymentResponse:
    """Deploy infrastructure for a customer."""
    config = config_storage.get(customer_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration for customer '{customer_id}' not found. "
            "Create a configuration first using POST /api/v1/configs",
        )

    stack_name = f"{customer_id}-{request.environment}"

    existing = db.get_deployment(customer_id, request.environment)
    if existing:
        if existing.status == DeploymentStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment {stack_name} is already in progress",
            )
        if existing.status == DeploymentStatus.DESTROYING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment {stack_name} is being destroyed. "
                "Wait for destruction to complete.",
            )

    if existing is None:
        try:
            db.create_deployment(
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

    background_tasks.add_task(run_deployment, config, request.environment, db)

    return DeploymentResponse(
        customer_id=customer_id,
        environment=request.environment,
        stack_name=stack_name,
        status=DeploymentStatus.PENDING,
        message="Deployment initiated. Check status endpoint for progress.",
    )


@router.get(
    "/{customer_id}/{environment}/status",
    response_model=CustomerDeployment,
    summary="Get deployment status",
)
async def get_deployment_status(
    customer_id: str,
    environment: str = "prod",
) -> CustomerDeployment:
    """Get the current deployment status (reads from local database)."""
    deployment = db.get_deployment(customer_id, environment)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment for {customer_id}-{environment} not found",
        )

    return CustomerDeployment(
        id=deployment.id,
        customer_id=deployment.customer_id,
        environment=deployment.environment,
        stack_name=deployment.stack_name,
        aws_region=deployment.aws_region,
        role_arn=deployment.role_arn,
        status=deployment.status,
        pulumi_deployment_id=deployment.pulumi_deployment_id,
        outputs=_parse_deployment_outputs(deployment.outputs),
        error_message=deployment.error_message,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


@router.get(
    "/{customer_id}",
    response_model=list[CustomerDeployment],
    summary="List customer deployments",
)
async def list_customer_deployments(customer_id: str) -> list[CustomerDeployment]:
    """List all deployments for a customer."""
    deployments = db.get_deployments_by_customer(customer_id)
    return [
        CustomerDeployment(
            id=d.id,
            customer_id=d.customer_id,
            environment=d.environment,
            stack_name=d.stack_name,
            aws_region=d.aws_region,
            role_arn=d.role_arn,
            status=d.status,
            pulumi_deployment_id=d.pulumi_deployment_id,
            outputs=_parse_deployment_outputs(d.outputs),
            error_message=d.error_message,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in deployments
    ]


async def run_destroy(
    customer_id: str,
    environment: str,
    database: Database,
) -> None:
    """Background task to destroy customer infrastructure via Automation API.

    Runs pre-destroy cleanup via SSM (delete ArgoCD apps, Karpenter nodepools,
    LoadBalancer services) before calling Pulumi destroy.
    """
    stack_name = f"{customer_id}-{environment}"

    try:
        engine = get_pulumi_engine()

        database.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.DESTROYING,
        )

        try:
            from api.services.destroy_manager import DestroyManager

            destroy_mgr = DestroyManager(customer_id, environment)
            logger.info("Running pre-destroy cleanup for %s", stack_name)
            cleanup_result = await destroy_mgr.run_pre_destroy()

            if cleanup_result.status.value == "failed":
                logger.warning(
                    "Pre-destroy cleanup failed for %s: %s. Proceeding with destroy anyway.",
                    stack_name,
                    cleanup_result.error,
                )
            else:
                logger.info("Pre-destroy cleanup succeeded for %s", stack_name)
        except Exception:
            logger.exception(
                "Pre-destroy cleanup error for %s. Proceeding with destroy anyway.",
                stack_name,
            )

        result = await asyncio.to_thread(engine.destroy, stack_name)

        if result.summary.result == "succeeded":
            database.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.DESTROYED,
                outputs="",
                error_message="",
            )
        else:
            database.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.FAILED,
                error_message=f"Destroy finished with result: {result.summary.result}",
            )

    except Exception as e:
        logger.exception("Destroy failed for %s", stack_name)
        database.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=f"Destroy failed: {e}",
        )


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
    background_tasks: BackgroundTasks,
) -> DeploymentResponse:
    """Destroy infrastructure for a customer."""
    stack_name = f"{customer_id}-{environment}"

    existing = db.get_deployment(customer_id, environment)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment {stack_name} not found",
        )

    if existing.status == DeploymentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} is in progress. "
            "Wait for it to complete before destroying.",
        )
    if existing.status == DeploymentStatus.DESTROYING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} is already being destroyed",
        )
    if existing.status == DeploymentStatus.DESTROYED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deployment {stack_name} has already been destroyed",
        )

    background_tasks.add_task(run_destroy, customer_id, environment, db)

    return DeploymentResponse(
        customer_id=customer_id,
        environment=environment,
        stack_name=stack_name,
        status=DeploymentStatus.DESTROYING,
        message="Destruction initiated. Check status endpoint for progress. "
        "This operation cannot be undone.",
    )
