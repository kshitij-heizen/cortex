
import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from api.database import db
from api.models import DeploymentStatus
from api.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Delay before installing addons (wait for access node user-data to complete)
ADDON_INSTALL_DELAY_SECONDS = 90

# States that allow processing deployment outcome (avoid duplicate webhook handling)
DEPLOYMENT_IN_PROGRESS_STATES = {DeploymentStatus.PENDING, DeploymentStatus.IN_PROGRESS}

# Also allow "succeeded" webhook when already SUCCEEDED (retries / late webhooks) so addon install still runs
DEPLOYMENT_ALLOW_SUCCEEDED_WEBHOOK = {
    DeploymentStatus.PENDING,
    DeploymentStatus.IN_PROGRESS,
    DeploymentStatus.SUCCEEDED,
}


def _webhook_response(**result: str | bool) -> dict:
    """Build standard webhook JSON response with received=True."""
    return {"received": True, **result}


def _verify_pulumi_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Pulumi webhook signature using HMAC-SHA256. Requires non-empty secret."""
    if not secret:
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    if signature.startswith("sha256="):
        return hmac.compare_digest(f"sha256={expected}", signature)
    return hmac.compare_digest(expected, signature)


def _parse_stack_name(stack_name: str) -> tuple[str, str] | None:
    """Parse customer_id and environment from stack name.

    Pulumi webhook sends stack name only, e.g. "cortex-dev".
    Format: "{customer_id}-{environment}"
    """
    stack_name = stack_name.strip()
    if not stack_name:
        return None

    parts = stack_name.rsplit("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None

    return parts[0], parts[1]


async def _handle_deployment_succeeded(
    customer_id: str,
    environment: str,
    stack_name: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """Handle successful deployment: save outputs and trigger addon installation."""
    from api.routes.deployments import get_pulumi_client

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

        # Trigger Karpenter + ArgoCD installation in background (with delay for user-data)
        background_tasks.add_task(_install_addons_after_delay, customer_id, environment)

        logger.info("Deployment %s succeeded, addon install triggered", stack_name)
        return {"processed": True, "action": "addons_triggered"}

    except Exception as e:
        logger.exception("Error processing successful deployment: %s", e)
        return {"processed": False, "reason": str(e)}


async def _install_addons_after_delay(customer_id: str, environment: str) -> None:
    """Wait for access node user-data to finish, then trigger addon install."""
    from api.services.addon_installer import AddonInstallerService

    logger.info(
        "Waiting %d seconds before installing addons for %s-%s",
        ADDON_INSTALL_DELAY_SECONDS,
        customer_id,
        environment,
    )
    await asyncio.sleep(ADDON_INSTALL_DELAY_SECONDS)

    try:
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
            "Addon install failed for %s-%s (can be retried manually)",
            customer_id,
            environment,
        )


async def _handle_deployment_failed(
    customer_id: str,
    environment: str,
    stack_name: str,
    error_message: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """Handle failed deployment: update status and trigger auto-destroy."""
    db.update_deployment_status(
        stack_name=stack_name,
        status=DeploymentStatus.FAILED,
        error_message=error_message,
    )
    logger.info("Deployment %s failed: %s", stack_name, error_message)

    logger.info("Triggering auto-destroy for failed deployment %s", stack_name)
    background_tasks.add_task(_trigger_auto_destroy, customer_id, environment, stack_name)

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
    background_tasks: BackgroundTasks,
    pulumi_webhook_signature: str = Header(default="", alias="Pulumi-Webhook-Signature"),
) -> dict:
    """Handle Pulumi deployment completion webhook."""
    payload = await request.body()

    secret = (settings.pulumi_webhook_secret or "").strip()
    if not secret:
        logger.warning("Webhook rejected: secret not configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook secret not configured",
        )

    if not _verify_pulumi_signature(payload, pulumi_webhook_signature, secret):
        logger.warning("Invalid webhook signature received")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    data = await request.json()

    stack_name = data.get("stackName", "")
    operation = data.get("operation", "update")
    deployment_status = data.get("status", "")
    error_message = data.get("message", "")

    logger.info(
        "Received Pulumi webhook: stack=%s, operation=%s, status=%s",
        stack_name,
        operation,
        deployment_status,
    )

    if not stack_name:
        return _webhook_response(processed=False, reason="missing stack")

    parsed = _parse_stack_name(stack_name)
    if not parsed:
        return _webhook_response(processed=False, reason="invalid stack format")

    customer_id, environment = parsed
    stack_name = f"{customer_id}-{environment}"

    deployment = db.get_deployment(customer_id, environment)
    if not deployment:
        logger.warning("No deployment found for %s", stack_name)
        return _webhook_response(processed=False, reason="deployment not found")

    result: dict
    if operation == "update":
        if deployment_status == "succeeded":
            if deployment.status not in DEPLOYMENT_ALLOW_SUCCEEDED_WEBHOOK:
                result = {
                    "processed": False,
                    "reason": f"ignored (current status: {deployment.status.value})",
                }
            else:
                result = await _handle_deployment_succeeded(
                    customer_id, environment, stack_name, background_tasks
                )
        elif deployment_status == "failed":
            if deployment.status not in DEPLOYMENT_IN_PROGRESS_STATES:
                result = {
                    "processed": False,
                    "reason": f"already processed (current status: {deployment.status.value})",
                }
            else:
                result = await _handle_deployment_failed(
                    customer_id,
                    environment,
                    stack_name,
                    error_message or "Deployment failed",
                    background_tasks,
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

    return _webhook_response(**result)


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