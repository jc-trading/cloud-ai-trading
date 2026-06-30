"""
Authentication API routes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, AdminUser, DB
from app.modules.auth.schemas import (
    UserRegister,
    UserLogin,
    UserUpdate,
    UserRoleUpdate,
    UserResponse,
    TokenResponse,
    TokenRefresh,
    MessageResponse,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: UserRegister, db: DB):
    """Register a new user account."""
    user = await AuthService.register(db, data)
    tokens = AuthService.generate_tokens(user)
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: DB):
    """Login with email and password."""
    user = await AuthService.authenticate(db, data.email, data.password)
    tokens = AuthService.generate_tokens(user)
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(data: TokenRefresh, db: DB):
    """Refresh access token using refresh token."""
    tokens = await AuthService.refresh_access_token(db, data.refresh_token)
    # Get user for response
    from app.core.security import decode_token
    from uuid import UUID

    payload = decode_token(tokens["access_token"])
    user = await AuthService.get_user_by_id(db, UUID(payload["sub"]))
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser):
    """Get current authenticated user info."""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(data: UserUpdate, current_user: CurrentUser, db: DB):
    """Update current user profile."""
    user = await AuthService.update_user(db, current_user, data)
    return UserResponse.model_validate(user)


# --- Admin Routes ---


@router.get("/users", response_model=list[UserResponse])
async def list_users(admin: AdminUser, db: DB, skip: int = 0, limit: int = 50):
    """List all users (admin only)."""
    users = await AuthService.list_users(db, skip, limit)
    return [UserResponse.model_validate(u) for u in users]


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(user_id: str, data: UserRoleUpdate, admin: AdminUser, db: DB):
    """Update a user's role (admin only)."""
    from uuid import UUID

    user = await AuthService.update_user_role(db, UUID(user_id), data.role)
    return UserResponse.model_validate(user)
