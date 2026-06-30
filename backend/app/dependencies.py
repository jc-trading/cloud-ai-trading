"""
Shared FastAPI dependencies: database session, current user, permission checks.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import decode_token
from app.core.exceptions import CredentialsException, PermissionDeniedException
from app.modules.auth.models import User, UserRole
from app.modules.auth.service import AuthService
from app.modules.auth.rbac import has_permission


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: str = Header(..., description="Bearer <token>"),
) -> User:
    """Extract and validate the current user from JWT token."""
    if not authorization.startswith("Bearer "):
        raise CredentialsException()

    token = authorization[7:]  # Remove "Bearer " prefix
    payload = decode_token(token)

    if not payload or payload.get("type") != "access":
        raise CredentialsException()

    user_id = payload.get("sub")
    if not user_id:
        raise CredentialsException()

    user = await AuthService.get_user_by_id(db, UUID(user_id))
    if not user.is_active:
        raise CredentialsException(detail="Account is disabled")

    return user


def require_permission(permission: str):
    """Dependency factory: require a specific permission."""

    async def _check_permission(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not has_permission(current_user.role, permission):
            raise PermissionDeniedException(
                detail=f"Permission '{permission}' required. Your role: {current_user.role.value}"
            )
        return current_user

    return _check_permission


def require_role(*roles: UserRole):
    """Dependency factory: require one of the specified roles."""

    async def _check_role(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in roles:
            raise PermissionDeniedException(
                detail=f"Required role: {[r.value for r in roles]}. Your role: {current_user.role.value}"
            )
        return current_user

    return _check_role


# Common dependency shortcuts
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_role(UserRole.SUPER_ADMIN, UserRole.ADMIN))]
DB = Annotated[AsyncSession, Depends(get_db)]
