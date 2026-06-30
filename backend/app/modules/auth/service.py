"""
Authentication service: user CRUD, login, token management.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.exceptions import CredentialsException, NotFoundException
from app.modules.auth.models import User, UserRole
from app.modules.auth.schemas import UserRegister, UserUpdate


class AuthService:

    @staticmethod
    async def register(db: AsyncSession, data: UserRegister) -> User:
        """Register a new user."""
        # Check if email already exists
        result = await db.execute(select(User).where(User.email == data.email))
        if result.scalar_one_or_none():
            raise CredentialsException(detail="Email already registered")

        user = User(
            name=data.name,
            email=data.email,
            hashed_password=hash_password(data.password),
            timezone=data.timezone,
            country=data.country,
            language=data.language,
            currency=data.currency,
            role=UserRole.BASIC,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    @staticmethod
    async def authenticate(db: AsyncSession, email: str, password: str) -> User:
        """Authenticate user by email and password."""
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise CredentialsException(detail="Invalid email or password")

        if not user.is_active:
            raise CredentialsException(detail="Account is disabled")

        return user

    @staticmethod
    def generate_tokens(user: User) -> dict:
        """Generate JWT access and refresh tokens for user."""
        token_data = {"sub": str(user.id), "role": user.role.value}
        return {
            "access_token": create_access_token(token_data),
            "refresh_token": create_refresh_token(token_data),
        }

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: UUID) -> User:
        """Get user by ID."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundException("User")
        return user

    @staticmethod
    async def refresh_access_token(db: AsyncSession, refresh_token: str) -> dict:
        """Generate new access token from refresh token."""
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise CredentialsException(detail="Invalid refresh token")

        user = await AuthService.get_user_by_id(db, UUID(payload["sub"]))
        return AuthService.generate_tokens(user)

    @staticmethod
    async def update_user(db: AsyncSession, user: User, data: UserUpdate) -> User:
        """Update user profile."""
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(user, key, value)
        await db.flush()
        await db.refresh(user)
        return user

    @staticmethod
    async def update_user_role(
        db: AsyncSession, user_id: UUID, role: UserRole
    ) -> User:
        """Update user role (admin only)."""
        user = await AuthService.get_user_by_id(db, user_id)
        user.role = role
        await db.flush()
        await db.refresh(user)
        return user

    @staticmethod
    async def list_users(db: AsyncSession, skip: int = 0, limit: int = 50) -> list[User]:
        """List all users (admin only)."""
        result = await db.execute(
            select(User).offset(skip).limit(limit).order_by(User.created_at.desc())
        )
        return list(result.scalars().all())
