"""
RBAC configuration using Casbin-style policy.
Lightweight implementation without external Casbin dependency for simplicity.
"""

from app.modules.auth.models import UserRole

# Permission definitions
PERMISSIONS = {
    "manage_users",
    "manage_system",
    "connect_exchange",
    "live_trading",
    "simulate_trading",
    "ai_analysis",
    "ai_analysis_limited",
    "quant_strategies",
    "view_market_data",
    "manage_watchlist",
}

# Role → Permission mapping
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.SUPER_ADMIN: {
        "manage_users",
        "manage_system",
        "connect_exchange",
        "live_trading",
        "simulate_trading",
        "ai_analysis",
        "quant_strategies",
        "view_market_data",
        "manage_watchlist",
    },
    UserRole.ADMIN: {
        "manage_users",
        "connect_exchange",
        "live_trading",
        "simulate_trading",
        "ai_analysis",
        "quant_strategies",
        "view_market_data",
        "manage_watchlist",
    },
    UserRole.PREMIUM: {
        "connect_exchange",
        "live_trading",
        "simulate_trading",
        "ai_analysis",
        "quant_strategies",
        "view_market_data",
        "manage_watchlist",
    },
    UserRole.BASIC: {
        "connect_exchange",
        "simulate_trading",
        "ai_analysis_limited",
        "view_market_data",
        "manage_watchlist",
    },
    UserRole.GUEST: {
        "view_market_data",
    },
}


def has_permission(role: UserRole, permission: str) -> bool:
    """Check if a role has a specific permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


def get_permissions(role: UserRole) -> set[str]:
    """Get all permissions for a role."""
    return ROLE_PERMISSIONS.get(role, set())
