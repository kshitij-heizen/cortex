import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt
from pymongo.errors import DuplicateKeyError

from api.auth_models import AuthResponse, UserResponse
from api.database import db
from api.settings import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expires_in_hours)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        return None


def register_user(name: str, email: str, password: str) -> AuthResponse:
    now = datetime.now(timezone.utc)

    doc: dict[str, Any] = {
        "email": email.lower().strip(),
        "name": name.strip(),
        "password_hash": hash_password(password),
        "avatar_url": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = db._users.insert_one(doc)
    except DuplicateKeyError:
        raise ValueError("A user with this email already exists")

    user_id = str(result.inserted_id)
    token = create_access_token(user_id, doc["email"])

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user_id,
            email=doc["email"],
            name=doc["name"],
            avatarUrl=doc["avatar_url"],
        ),
    )


def authenticate_user(email: str, password: str) -> AuthResponse:
    user = db._users.find_one({"email": email.lower().strip()})

    if not user:
        raise ValueError("Invalid email or password")

    if not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid email or password")

    user_id = str(user["_id"])
    token = create_access_token(user_id, user["email"])

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user_id,
            email=user["email"],
            name=user["name"],
            avatarUrl=user.get("avatar_url"),
        ),
    )


def get_user_by_id(user_id: str) -> Optional[UserResponse]:
    from bson import ObjectId

    try:
        user = db._users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None

    if not user:
        return None

    return UserResponse(
        id=str(user["_id"]),
        email=user["email"],
        name=user["name"],
        avatarUrl=user.get("avatar_url"),
    )
