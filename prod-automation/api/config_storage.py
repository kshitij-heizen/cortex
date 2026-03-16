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
    def save(self, customer_id: str, config: CustomerConfigResolved) -> None:
        pass

    @abstractmethod
    def get(self, customer_id: str) -> Optional[CustomerConfigResolved]:
        pass

    @abstractmethod
    def delete(self, customer_id: str) -> bool:
        pass

    @abstractmethod
    def list_all(self) -> list[CustomerConfigResolved]:
        pass

    @abstractmethod
    def exists(self, customer_id: str) -> bool:
        pass


class MongoConfigStorage(ConfigStorageBackend):
    """MongoDB-backed configuration storage."""

    def __init__(self, uri: str | None = None, db_name: str | None = None) -> None:
        self._uri = uri or settings.mongodb_uri
        self._db_name = db_name or settings.mongodb_database
        self._client: MongoClient[dict[str, Any]] = MongoClient(self._uri)
        self._db = self._client[self._db_name]
        self._configs: Collection[dict[str, Any]] = self._db["configs"]

        self._configs.create_index("customer_id", unique=True)

    def save(self, customer_id: str, config: CustomerConfigResolved) -> None:
        doc = config.model_dump(mode="json")
        doc["customer_id"] = customer_id
        self._configs.replace_one({"customer_id": customer_id}, doc, upsert=True)

    def get(self, customer_id: str) -> Optional[CustomerConfigResolved]:
        doc = self._configs.find_one({"customer_id": customer_id})
        if doc is None:
            return None
        doc.pop("_id", None)
        return CustomerConfigResolved.model_validate(doc)

    def delete(self, customer_id: str) -> bool:
        result = self._configs.delete_one({"customer_id": customer_id})
        return result.deleted_count > 0

    def list_all(self) -> list[CustomerConfigResolved]:
        configs: list[CustomerConfigResolved] = []
        for doc in self._configs.find():
            doc.pop("_id", None)
            try:
                configs.append(CustomerConfigResolved.model_validate(doc))
            except Exception:
                logger.warning("Skipping invalid config doc: %s", doc.get("customer_id"))
        return configs

    def exists(self, customer_id: str) -> bool:
        return self._configs.count_documents({"customer_id": customer_id}, limit=1) > 0


config_storage = MongoConfigStorage()
