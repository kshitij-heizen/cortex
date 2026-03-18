"""Customer configuration storage backed by MongoDB."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from pymongo import MongoClient
from pymongo.collection import Collection

from api.models import CustomerConfigResolved
from api.settings import settings

logger = logging.getLogger(__name__)


class ConfigStorageBackend(ABC):
    @abstractmethod
    def save(self, user_id: str, customer_id: str, config: CustomerConfigResolved) -> None:
        pass

    @abstractmethod
    def get(self, user_id: str, customer_id: str) -> Optional[CustomerConfigResolved]:
        pass

    @abstractmethod
    def delete(self, user_id: str, customer_id: str) -> bool:
        pass

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[CustomerConfigResolved]:
        pass

    @abstractmethod
    def exists(self, user_id: str, customer_id: str) -> bool:
        pass


class MongoConfigStorage(ConfigStorageBackend):
    """MongoDB-backed configuration storage."""

    def __init__(self, uri: str | None = None, db_name: str | None = None) -> None:
        self._uri = uri or settings.mongodb_uri
        self._db_name = db_name or settings.mongodb_database
        self._client: MongoClient[dict[str, Any]] = MongoClient(self._uri)
        self._db = self._client[self._db_name]
        self._configs: Collection[dict[str, Any]] = self._db["configs"]

        # Compound unique index: each user has their own namespace for customer_ids
        self._configs.create_index(
            [("user_id", 1), ("customer_id", 1)],
            unique=True,
        )
        self._configs.create_index("user_id")

    def save(self, user_id: str, customer_id: str, config: CustomerConfigResolved) -> None:
        doc = config.model_dump(mode="json")
        doc["customer_id"] = customer_id
        doc["user_id"] = user_id
        self._configs.replace_one(
            {"user_id": user_id, "customer_id": customer_id},
            doc,
            upsert=True,
        )

    def get(self, user_id: str, customer_id: str) -> Optional[CustomerConfigResolved]:
        doc = self._configs.find_one({"user_id": user_id, "customer_id": customer_id})
        if doc is None:
            return None
        doc.pop("_id", None)
        doc.pop("user_id", None)
        return CustomerConfigResolved.model_validate(doc)

    def delete(self, user_id: str, customer_id: str) -> bool:
        result = self._configs.delete_one({"user_id": user_id, "customer_id": customer_id})
        return result.deleted_count > 0

    def list_by_user(self, user_id: str) -> list[CustomerConfigResolved]:
        configs: list[CustomerConfigResolved] = []
        for doc in self._configs.find({"user_id": user_id}):
            doc.pop("_id", None)
            doc.pop("user_id", None)
            try:
                configs.append(CustomerConfigResolved.model_validate(doc))
            except Exception:
                logger.warning("Skipping invalid config doc: %s", doc.get("customer_id"))
        return configs

    def exists(self, user_id: str, customer_id: str) -> bool:
        return (
            self._configs.count_documents(
                {"user_id": user_id, "customer_id": customer_id}, limit=1
            )
            > 0
        )

    def get_by_customer_id(self, customer_id: str) -> Optional[CustomerConfigResolved]:
        """Get config by customer_id only (for system/worker use, no user filter)."""
        doc = self._configs.find_one({"customer_id": customer_id})
        if doc is None:
            return None
        doc.pop("_id", None)
        doc.pop("user_id", None)
        return CustomerConfigResolved.model_validate(doc)


config_storage = MongoConfigStorage()
