"""MongoDB database for tracking customer deployments."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from api.models import DeploymentStatus
from api.settings import settings

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 2700


class Database:
    """MongoDB-backed database for deployment tracking."""

    def __init__(self, uri: str | None = None, db_name: str | None = None) -> None:
        self._uri = uri or settings.mongodb_uri
        self._db_name = db_name or settings.mongodb_database
        self._client: MongoClient[dict[str, Any]] = MongoClient(self._uri)
        self._db = self._client[self._db_name]
        self._deployments: Collection[dict[str, Any]] = self._db["deployments"]
        self._locks: Collection[dict[str, Any]] = self._db["locks"]
        self._users: Collection[dict[str, Any]] = self._db["users"]

        self._deployments.create_index("stack_name", unique=True)
        self._deployments.create_index("customer_id")
        self._deployments.create_index("user_id")
        self._locks.create_index("stack_name", unique=True)
        self._locks.create_index("expires_at", expireAfterSeconds=0)
        self._users.create_index("email", unique=True)

    def create_deployment(
        self,
        user_id: str,
        customer_id: str,
        environment: str,
        aws_region: str,
        role_arn: str,
    ) -> dict[str, Any]:
        stack_name = f"{customer_id}-{environment}"

        existing = self._deployments.find_one({"stack_name": stack_name})
        if existing:
            raise ValueError(f"Deployment {stack_name} already exists")

        now = datetime.now(timezone.utc)
        doc: dict[str, Any] = {
            "user_id": user_id,
            "customer_id": customer_id,
            "environment": environment,
            "stack_name": stack_name,
            "aws_region": aws_region,
            "role_arn": role_arn,
            "status": DeploymentStatus.PENDING.value,
            "pulumi_deployment_id": None,
            "outputs": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        result = self._deployments.insert_one(doc)
        doc["_id"] = result.inserted_id
        doc["status"] = DeploymentStatus(doc["status"])
        return doc

    def get_deployment(
        self, customer_id: str, environment: str
    ) -> Optional[dict[str, Any]]:
        stack_name = f"{customer_id}-{environment}"
        doc = self._deployments.find_one({"stack_name": stack_name})
        if doc:
            doc["status"] = DeploymentStatus(doc["status"])
        return doc

    def get_deployment_for_user(
        self, user_id: str, customer_id: str, environment: str
    ) -> Optional[dict[str, Any]]:
        stack_name = f"{customer_id}-{environment}"
        doc = self._deployments.find_one({"stack_name": stack_name, "user_id": user_id})
        if doc:
            doc["status"] = DeploymentStatus(doc["status"])
        return doc

    def get_deployment_by_stack(self, stack_name: str) -> Optional[dict[str, Any]]:
        doc = self._deployments.find_one({"stack_name": stack_name})
        if doc:
            doc["status"] = DeploymentStatus(doc["status"])
        return doc

    def update_deployment_status(
        self,
        stack_name: str,
        status: DeploymentStatus,
        pulumi_deployment_id: Optional[str] = None,
        outputs: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        update: dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.now(timezone.utc),
        }

        if pulumi_deployment_id is not None:
            update["pulumi_deployment_id"] = pulumi_deployment_id
        if outputs is not None:
            update["outputs"] = outputs
        if error_message is not None:
            update["error_message"] = error_message

        result = self._deployments.find_one_and_update(
            {"stack_name": stack_name},
            {"$set": update},
            return_document=True,
        )
        if result:
            result["status"] = DeploymentStatus(result["status"])
        return result

    def get_deployments_by_customer(self, customer_id: str) -> list[dict[str, Any]]:
        docs = list(self._deployments.find({"customer_id": customer_id}))
        for doc in docs:
            doc["status"] = DeploymentStatus(doc["status"])
        return docs

    def get_deployments_for_user(self, user_id: str) -> list[dict[str, Any]]:
        docs = list(self._deployments.find({"user_id": user_id}))
        for doc in docs:
            doc["status"] = DeploymentStatus(doc["status"])
        return docs

    def acquire_lock(self, stack_name: str, operation: str) -> bool:
        now = datetime.now(timezone.utc)
        try:
            self._locks.insert_one(
                {
                    "stack_name": stack_name,
                    "operation": operation,
                    "acquired_at": now,
                    "expires_at": now + timedelta(seconds=LOCK_TTL_SECONDS),
                }
            )
            logger.info("Lock acquired for %s (%s)", stack_name, operation)
            return True
        except DuplicateKeyError:
            logger.warning("Lock already held for %s — cannot start %s", stack_name, operation)
            return False

    def release_lock(self, stack_name: str) -> bool:
        result = self._locks.delete_one({"stack_name": stack_name})
        released = result.deleted_count > 0
        if released:
            logger.info("Lock released for %s", stack_name)
        return released

    def audit_log(
        self,
        action: str,
        customer_id: str,
        *,
        user_id: str = "",
        environment: str = "",
        details: str = "",
        actor: str = "system",
    ) -> None:
        self._db["audit_log"].insert_one(
            {
                "action": action,
                "customer_id": customer_id,
                "user_id": user_id,
                "environment": environment,
                "details": details,
                "actor": actor,
                "timestamp": datetime.now(timezone.utc),
            }
        )


db = Database()
