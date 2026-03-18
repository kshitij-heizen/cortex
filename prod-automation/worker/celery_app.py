"""Celery application and task definitions for BYOC platform."""

import asyncio
import json
import logging
import os
import time

from celery import Celery
from celery.signals import worker_process_init

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "byoc",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=3600,
    task_time_limit=4200,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@worker_process_init.connect
def _init_worker(**kwargs):
    from dotenv import load_dotenv

    load_dotenv()
    logger.info("Worker process initialized")


@celery_app.task(
    bind=True,
    name="byoc.deploy",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def deploy_task(self, customer_id: str, environment: str) -> dict:
    from api.config_storage import config_storage
    from api.database import db
    from api.models import DeploymentStatus
    from api.pulumi_engine import PulumiEngine
    from api.settings import settings

    stack_name = f"{customer_id}-{environment}"
    logger.info("Starting deploy task for %s", stack_name)

    if not db.acquire_lock(stack_name, "deploy"):
        logger.error("Cannot deploy %s — lock already held", stack_name)
        return {"status": "locked", "stack_name": stack_name}

    try:
        config = config_storage.get_by_customer_id(customer_id)
        if not config:
            raise ValueError(f"Customer config not found: {customer_id}")

        engine = PulumiEngine(
            backend_url=settings.pulumi_backend_url,
            secrets_provider=settings.pulumi_secrets_provider,
            work_dir=settings.pulumi_work_dir,
        )

        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.IN_PROGRESS,
        )
        db.audit_log("deploy_started", customer_id, environment=environment)

        result = engine.deploy(stack_name, config)

        if result.summary.result != "succeeded":
            db.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.FAILED,
                error_message=f"Pulumi up finished with result: {result.summary.result}",
            )
            return {"status": "failed", "stack_name": stack_name}

        outputs = engine.get_outputs(stack_name)
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.SUCCEEDED,
            outputs=json.dumps(outputs),
            error_message="",
        )

        try:
            from api.services.gitops_writer import GitOpsWriter

            writer = GitOpsWriter(config, outputs)
            writer.push_to_github()
            logger.info("GitOps values pushed for %s", stack_name)
        except Exception:
            logger.exception("GitOps write failed for %s", stack_name)

        addon_delay = 90
        logger.info("Waiting %ds for access node boot...", addon_delay)
        time.sleep(addon_delay)

        try:
            if config.addons and config.addons.argocd and config.addons.argocd.enabled:
                from api.services.addon_installer import AddonInstallerService

                installer = AddonInstallerService(customer_id, environment)
                addon_result = _run_async(installer.install_all_addons())
                logger.info(
                    "Addon install triggered for %s: command_id=%s",
                    stack_name,
                    addon_result.ssm_command_id,
                )
        except Exception:
            logger.exception("Addon install failed for %s", stack_name)

        db.audit_log("deploy_succeeded", customer_id, environment=environment)
        return {"status": "succeeded", "stack_name": stack_name}

    except Exception as e:
        logger.exception("Deploy failed for %s", stack_name)
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=f"Deploy failed: {e}",
        )
        db.audit_log("deploy_failed", customer_id, environment=environment, details=str(e))
        return {"status": "failed", "stack_name": stack_name, "error": str(e)}
    finally:
        db.release_lock(stack_name)


@celery_app.task(
    bind=True,
    name="byoc.destroy",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def destroy_task(self, customer_id: str, environment: str) -> dict:
    from api.database import db
    from api.models import DeploymentStatus
    from api.pulumi_engine import PulumiEngine
    from api.settings import settings

    stack_name = f"{customer_id}-{environment}"
    logger.info("Starting destroy task for %s", stack_name)

    if not db.acquire_lock(stack_name, "destroy"):
        logger.error("Cannot destroy %s — lock already held", stack_name)
        return {"status": "locked", "stack_name": stack_name}

    try:
        engine = PulumiEngine(
            backend_url=settings.pulumi_backend_url,
            secrets_provider=settings.pulumi_secrets_provider,
            work_dir=settings.pulumi_work_dir,
        )

        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.DESTROYING,
        )
        db.audit_log("destroy_started", customer_id, environment=environment)

        try:
            from api.services.destroy_manager import DestroyManager

            destroy_mgr = DestroyManager(customer_id, environment)
            logger.info("Running pre-destroy cleanup for %s", stack_name)
            cleanup_result = _run_async(destroy_mgr.run_pre_destroy())

            if cleanup_result.status.value == "failed":
                logger.warning(
                    "Pre-destroy cleanup failed for %s: %s. Proceeding anyway.",
                    stack_name,
                    cleanup_result.error,
                )
            else:
                logger.info("Pre-destroy cleanup succeeded for %s", stack_name)
        except Exception:
            logger.exception("Pre-destroy cleanup error for %s. Proceeding anyway.", stack_name)

        result = engine.destroy(stack_name)

        if result.summary.result == "succeeded":
            db.update_deployment_status(
                stack_name=stack_name,
                status=DeploymentStatus.DESTROYED,
                outputs="",
                error_message="",
            )
            db.audit_log("destroy_succeeded", customer_id, environment=environment)
            return {"status": "destroyed", "stack_name": stack_name}

        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=f"Destroy finished with result: {result.summary.result}",
        )
        db.audit_log(
            "destroy_failed",
            customer_id,
            environment=environment,
            details=f"result: {result.summary.result}",
        )
        return {"status": "failed", "stack_name": stack_name}

    except Exception as e:
        logger.exception("Destroy failed for %s", stack_name)
        db.update_deployment_status(
            stack_name=stack_name,
            status=DeploymentStatus.FAILED,
            error_message=f"Destroy failed: {e}",
        )
        db.audit_log("destroy_failed", customer_id, environment=environment, details=str(e))
        return {"status": "failed", "stack_name": stack_name, "error": str(e)}
    finally:
        db.release_lock(stack_name)


@celery_app.task(
    bind=True,
    name="byoc.install_addons",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def install_addons_task(self, customer_id: str, environment: str) -> dict:
    from api.services.addon_installer import AddonInstallerService

    stack_name = f"{customer_id}-{environment}"
    logger.info("Starting addon install task for %s", stack_name)

    try:
        installer = AddonInstallerService(customer_id, environment)
        result = _run_async(installer.install_all_addons())
        return {
            "status": result.status.value,
            "stack_name": stack_name,
            "ssm_command_id": result.ssm_command_id,
        }
    except Exception as e:
        logger.exception("Addon install failed for %s", stack_name)
        return {"status": "failed", "stack_name": stack_name, "error": str(e)}

