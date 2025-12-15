"""
FastAPI Authentication Dependencies

Provides authentication and authorization dependencies for FastAPI routes.
"""

import logging
from typing import Optional
from fastapi import Depends, HTTPException, status, Cookie, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..core.database import DatabaseManager
from ..core.config import get_config
from ..auth.auth_manager import AuthManager
from ..core.exceptions import AuthenticationError, SessionExpiredError, UnauthorizedError


logger = logging.getLogger(__name__)


# HTTP Bearer scheme for Authorization header (optional, we use cookies)
bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> DatabaseManager:
    """
    Dependency to get database manager.

    Returns:
        DatabaseManager instance
    """
    config = get_config()
    return DatabaseManager(config.database.connection_string)


def get_auth_manager(db: DatabaseManager = Depends(get_db)) -> AuthManager:
    """
    Dependency to get auth manager.

    Args:
        db: DatabaseManager instance

    Returns:
        AuthManager instance
    """
    config = get_config()
    return AuthManager(db, config.jwt_secret_key)


async def get_current_user(
    request: Request,
    session_token: Optional[str] = Cookie(None, alias="session_token"),
    auth: AuthManager = Depends(get_auth_manager)
) -> dict:
    """
    Dependency to get current authenticated user.

    Verifies JWT session token from cookie and returns user info.

    Args:
        request: FastAPI request
        session_token: JWT session token from cookie
        auth: AuthManager instance

    Returns:
        User info dictionary with:
            - email: User email
            - role: User role (admin/manager/user)
            - session_id: Session ID
            - display_name: Display name

    Raises:
        HTTPException: If not authenticated or session expired
    """
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        # Verify session
        user_info = auth.verify_session(session_token)

        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
                headers={"WWW-Authenticate": "Bearer"}
            )

        return user_info

    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired, please login again"
        )
    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


async def get_current_user_optional(
    session_token: Optional[str] = Cookie(None, alias="session_token"),
    auth: AuthManager = Depends(get_auth_manager)
) -> Optional[dict]:
    """
    Dependency to get current user (optional, returns None if not authenticated).

    Args:
        session_token: JWT session token from cookie
        auth: AuthManager instance

    Returns:
        User info dictionary or None
    """
    if not session_token:
        return None

    try:
        user_info = auth.verify_session(session_token)
        return user_info
    except:
        return None


async def require_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Dependency to require admin role.

    Args:
        current_user: Current user from get_current_user

    Returns:
        User info dictionary

    Raises:
        HTTPException: If user is not admin
    """
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return current_user


async def require_manager_or_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Dependency to require manager or admin role.

    Args:
        current_user: Current user from get_current_user

    Returns:
        User info dictionary

    Raises:
        HTTPException: If user is not manager or admin
    """
    role = current_user.get("role")
    if role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required"
        )

    return current_user


def check_permission(permission: str):
    """
    Factory function to create permission check dependency.

    Args:
        permission: Permission name to check

    Returns:
        Dependency function

    Usage:
        @app.get("/admin/users", dependencies=[Depends(check_permission("manage_users"))])
    """
    async def _check_permission(
        current_user: dict = Depends(get_current_user),
        auth: AuthManager = Depends(get_auth_manager)
    ):
        email = current_user.get("email")

        if not auth.has_permission(email, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission}"
            )

        return current_user

    return _check_permission
