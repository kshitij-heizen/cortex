"""Webhook endpoints for external service integrations."""

import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from api.database import db
from api.models import DeploymentStatus
from api.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Delay before installing addons (wait for access node user-data to complete)
ADDON_INSTALL_DELAY_SECONDS = 90

# States that allow processing deployment outcome (avoid duplicate webhook handling)
DEPLOYMENT_IN_PROGRESS_STATES = {DeploymentStatus.PENDING, DeploymentStatus.IN_PROGRESS}


def _verify_pulumi_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Pulumi webhook signature using HMAC-SHA256."""
    if not secret:
        return True

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    if signature.startswith("sha256="):
        return hmac.compare_digest(f"sha256={expected}", signature)
    return hmac.compare_digest(expected, signature)


def _parse_stack_name(stack_fqn: str) -> tuple[str, str] | None:
    """Parse customer_id and environment from stack fully-qualified name.

    Stack FQN format: "org/project/stack-name"
    Stack name format: "{customer_id}-{environment}"
    """
    parts = stack_fqn.split("/")
    if len(parts) != 3:
        return None

    stack_name = parts[2]
    stack_parts = stack_name.rsplit("-", 1)
    if len(stack_parts) != 2:
        return None

    return stack_parts[0], stack_parts[1]


async def _handle_deployment_succeeded(
    customer_id: str,
    environment: str,
    stack_name: str,
) -> dict:
    """Handle successful deployment: save outputs and trigger addon installation."""
    from api.routes.deployments import _auto_install_addons_after_delay, get_pulumi_client

    try:
        client = get_pulumi_client()

        outputs = await client.get_stack_outputs(
            project_name=settings.pulumi_project,
            stack_name=stack_name,
        )

        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.SUCCEEDED,
            outputs=json.dumps(outputs),
            error_message="",
        )

        # Trigger ArgoCD installation in background (with delay for user-data)
        asyncio.create_task(_auto_install_addons_after_delay(customer_id, environment))

        logger.info("Deployment %s succeeded, ArgoCD install triggered", stack_name)
        return {"processed": True, "action": "addons_triggered"}

    except Exception as e:
        logger.exception("Error processing successful deployment: %s", e)
        return {"processed": False, "reason": str(e)}


async def _handle_deployment_failed(
    customer_id: str,
    environment: str,
    stack_name: str,
    error_message: str,
) -> dict:
    """Handle failed deployment: update status and trigger auto-destroy."""
    db.update_deployment_status(
        stack_name=stack_name,
        status=DeploymentStatus.FAILED,
        error_message=error_message,
    )
    logger.info("Deployment %s failed: %s", stack_name, error_message)

    # Auto-destroy to clean up partial resources
    logger.info("Triggering auto-destroy for failed deployment %s", stack_name)
    asyncio.create_task(_trigger_auto_destroy(customer_id, environment, stack_name))

    return {"processed": True, "action": "marked_failed_and_destroy_triggered"}


async def _trigger_auto_destroy(
    customer_id: str,
    environment: str,
    stack_name: str,
) -> None:
    """Trigger automatic destroy after a failed deployment."""
    from api.routes.deployments import get_pulumi_client

    try:
        logger.info("Starting auto-destroy for %s", stack_name)

        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.DESTROYING,
        )

        client = get_pulumi_client()
        result = await client.trigger_deployment(
            project_name=settings.pulumi_project,
            stack_name=stack_name,
            operation="destroy",
        )

        deployment_id = result.get("id", "")
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.DESTROYING,
            pulumi_deployment_id=deployment_id,
        )

        logger.info("Auto-destroy triggered for %s, deployment_id=%s", stack_name, deployment_id)

    except Exception as e:
        logger.exception("Auto-destroy failed for %s: %s", stack_name, e)
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=f"Auto-destroy failed: {e}",
        )


def _handle_destroy_succeeded(stack_name: str) -> dict:
    """Handle successful destroy: update status in database."""
    db.update_deployment_status(
        stack_name=stack_name,
        status=DeploymentStatus.DESTROYED,
        outputs="",
        error_message="",
    )
    logger.info("Deployment %s destroyed successfully", stack_name)
    return {"processed": True, "action": "marked_destroyed"}


def _handle_destroy_failed(stack_name: str, error_message: str) -> dict:
    """Handle failed destroy: update status in database."""
    db.update_deployment_status(
        stack_name=stack_name,
        status=DeploymentStatus.FAILED,
        error_message=f"Destroy failed: {error_message}",
    )
    logger.info("Deployment %s destroy failed: %s", stack_name, error_message)
    return {"processed": True, "action": "marked_failed"}


@router.post(
    "/pulumi/deployment",
    summary="Pulumi deployment webhook",
    description="Receives deployment completion events from Pulumi Cloud. "
    "Automatically updates deployment status, triggers addon installation on success, "
    "and auto-destroys on failure.",
)
async def pulumi_deployment_webhook(
    request: Request,
    pulumi_webhook_signature: str = Header(default="", alias="Pulumi-Webhook-Signature"),
) -> dict:
    """Handle Pulumi deployment completion webhook."""
    payload = await request.body()

    if settings.pulumi_webhook_secret:
        if not _verify_pulumi_signature(
            payload, pulumi_webhook_signature, settings.pulumi_webhook_secret
        ):
            logger.warning("Invalid webhook signature received")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

    data = await request.json()

    stack_fqn = data.get("stack", "")
    operation = data.get("operation", "update")
    deployment_status = data.get("status", "")
    error_message = data.get("message", "")

    logger.info(
        "Received Pulumi webhook: stack=%s, operation=%s, status=%s",
        stack_fqn, operation, deployment_status,
    )

    if not stack_fqn:
        return {"received": True, "processed": False, "reason": "missing stack"}

    parsed = _parse_stack_name(stack_fqn)
    if not parsed:
        return {"received": True, "processed": False, "reason": "invalid stack format"}

    customer_id, environment = parsed
    stack_name = f"{customer_id}-{environment}"

    deployment = db.get_deployment(customer_id, environment)
    if not deployment:
        logger.warning("No deployment found for %s", stack_name)
        return {"received": True, "processed": False, "reason": "deployment not found"}

    result: dict
    if operation == "update":
        if deployment_status == "succeeded":
            if deployment.status not in DEPLOYMENT_IN_PROGRESS_STATES:
                result = {
                    "processed": False,
                    "reason": f"already processed (current status: {deployment.status.value})",
                }
            else:
                result = await _handle_deployment_succeeded(customer_id, environment, stack_name)
        elif deployment_status == "failed":
            if deployment.status not in DEPLOYMENT_IN_PROGRESS_STATES:
                result = {
                    "processed": False,
                    "reason": f"already processed (current status: {deployment.status.value})",
                }
            else:
                result = await _handle_deployment_failed(
                    customer_id, environment, stack_name, error_message or "Deployment failed"
                )
        else:
            result = {"processed": False, "reason": f"unhandled status: {deployment_status}"}

    elif operation == "destroy":
        if deployment_status == "succeeded":
            if deployment.status != DeploymentStatus.DESTROYING:
                result = {
                    "processed": False,
                    "reason": f"ignored (current status: {deployment.status.value}, expected destroying)",
                }
            else:
                result = _handle_destroy_succeeded(stack_name)
        elif deployment_status == "failed":
            if deployment.status != DeploymentStatus.DESTROYING:
                result = {
                    "processed": False,
                    "reason": f"ignored (current status: {deployment.status.value}, expected destroying)",
                }
            else:
                result = _handle_destroy_failed(stack_name, error_message or "Destroy failed")
        else:
            result = {"processed": False, "reason": f"unhandled status: {deployment_status}"}

    elif operation == "preview":
        result = {"processed": False, "reason": "preview operations are ignored"}

    else:
        result = {"processed": False, "reason": f"unhandled operation: {operation}"}

    return {"received": True, **result}


@router.get(
    "/pulumi/health",
    summary="Webhook health check",
)
async def webhook_health() -> dict:
    """Health check for webhook endpoint."""
    return {
        "status": "healthy",
        "webhook_secret_configured": bool(settings.pulumi_webhook_secret),
    }