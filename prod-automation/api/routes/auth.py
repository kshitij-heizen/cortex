import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth_models import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from api.dependencies import get_current_user
from api.services.auth_service import authenticate_user, register_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post(
    "/password-login",
    response_model=AuthResponse,
    summary="Login with email and password",
    responses={
        200: {"description": "Login successful"},
        401: {"description": "Invalid credentials"},
    },
)
async def login(request: LoginRequest) -> AuthResponse:
    try:
        return authenticate_user(request.email, request.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )


@router.post(
    "/password-register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register with email and password",
    responses={
        201: {"description": "Registration successful"},
        409: {"description": "Email already registered"},
    },
)
async def register(request: RegisterRequest) -> AuthResponse:
    try:
        return register_user(request.name, request.email, request.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user",
    responses={
        200: {"description": "Current user info"},
        401: {"description": "Not authenticated"},
    },
)
async def me(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return current_user
