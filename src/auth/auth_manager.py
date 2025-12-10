"""
Authentication manager for password-based auth and role-based access control.

This module handles:
- Password-based authentication (simple for testing)
- JWT token generation and validation
- Role-based access control (admin, manager, user)
- Session management
"""

import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict
import os
import logging

from src.core.database import DatabaseManager, UserSession
from src.core.exceptions import AuthenticationError, SessionExpiredError, UnauthorizedError
from src.utils.validators import validate_email, validate_domain


class AuthManager:
    """
    Manages user authentication and sessions.

    For testing/development, supports password-less login for @townsquaremedia.com emails.
    For production, can enforce password validation.
    """

    def __init__(self, database_manager: DatabaseManager, jwt_secret: str, session_timeout_hours: int = 8):
        """
        Initialize auth manager.

        Args:
            database_manager: Database manager instance
            jwt_secret: Secret key for JWT signing
            session_timeout_hours: Session timeout in hours (default: 8)
        """
        self.db = database_manager
        self.jwt_secret = jwt_secret
        self.session_timeout_hours = session_timeout_hours
        self.logger = logging.getLogger(__name__)

    def login(
        self, email: str, password: Optional[str] = None, ip_address: Optional[str] = None, user_agent: Optional[str] = None
    ) -> Optional[str]:
        """
        Authenticate user and create session.

        For testing: password is optional if email is from allowed domain.
        For production: password validation should be enforced.

        Args:
            email: User email address
            password: Password (optional for testing)
            ip_address: User IP address
            user_agent: User agent string

        Returns:
            JWT session token if successful, None otherwise

        Raises:
            AuthenticationError: If authentication fails
        """
        # Validate email format
        if not validate_email(email):
            self.logger.warning(f"Invalid email format: {email}")
            raise AuthenticationError("Invalid email format")

        # Validate domain
        if not self._validate_domain(email):
            self.logger.warning(f"Email from unauthorized domain: {email}")
            raise AuthenticationError("Email domain not authorized")

        # TODO: In production, validate password here
        # For now, we allow password-less login for testing
        if password:
            # Placeholder for password validation
            # In production, check against hashed password in database
            pass

        # Get user role
        user_role = self.get_user_role(email)

        # Generate JWT token
        session_token = self._generate_jwt(email, user_role)

        # Create session in database
        expires_at = datetime.utcnow() + timedelta(hours=self.session_timeout_hours)

        try:
            self.db.create_session(
                user_email=email,
                session_token=session_token,
                auth_method="password",
                user_role=user_role,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=expires_at,
            )

            self.logger.info(f"User logged in: {email} (role: {user_role})")
            return session_token

        except Exception as e:
            self.logger.error(f"Failed to create session: {e}")
            raise AuthenticationError("Failed to create session")

    def verify_session(self, session_token: str) -> Optional[Dict]:
        """
        Verify JWT token and return user info.

        Args:
            session_token: JWT token to verify

        Returns:
            User info dictionary or None if invalid:
            {
                'email': str,
                'role': str,
                'session_id': int
            }

        Raises:
            SessionExpiredError: If session is expired
            AuthenticationError: If token is invalid
        """
        try:
            # Decode JWT
            payload = jwt.decode(self.jwt_secret, session_token, algorithms=["HS256"])

            email = payload.get("email")
            exp = payload.get("exp")

            # Check expiration
            if exp and datetime.utcfromtimestamp(exp) < datetime.utcnow():
                self.logger.warning(f"Session expired for {email}")
                raise SessionExpiredError("Session expired")

            # Check session in database
            session = self.db.get_session_by_token(session_token)

            if not session:
                self.logger.warning(f"Session not found in database for {email}")
                raise AuthenticationError("Invalid session")

            if session.logout_at:
                self.logger.warning(f"Session already logged out for {email}")
                raise AuthenticationError("Session already logged out")

            if session.expires_at < datetime.utcnow():
                self.logger.warning(f"Session expired for {email}")
                raise SessionExpiredError("Session expired")

            # Update last activity
            db_session = self.db.get_session()
            try:
                session.last_activity = datetime.utcnow()
                db_session.commit()
            finally:
                db_session.close()

            return {
                "email": session.user_email,
                "role": session.user_role,
                "session_id": session.id,
                "display_name": session.display_name,
            }

        except jwt.ExpiredSignatureError:
            raise SessionExpiredError("JWT token expired")
        except jwt.InvalidTokenError as e:
            self.logger.error(f"Invalid JWT token: {e}")
            raise AuthenticationError("Invalid token")

    def logout(self, session_token: str) -> bool:
        """
        Logout user by invalidating session.

        Args:
            session_token: JWT token to invalidate

        Returns:
            True if successful, False otherwise
        """
        try:
            session = self.db.get_session_by_token(session_token)

            if not session:
                return False

            # Mark session as logged out
            db_session = self.db.get_session()
            try:
                session.logout_at = datetime.utcnow()
                db_session.commit()
                self.logger.info(f"User logged out: {session.user_email}")
                return True
            finally:
                db_session.close()

        except Exception as e:
            self.logger.error(f"Failed to logout: {e}")
            return False

    def get_user_role(self, email: str) -> str:
        """
        Get user role from environment variables.

        Roles are defined in ADMIN_USERS and MANAGER_USERS env vars.

        Args:
            email: User email address

        Returns:
            Role: 'admin', 'manager', or 'user'
        """
        admin_users_str = os.getenv("ADMIN_USERS", "")
        manager_users_str = os.getenv("MANAGER_USERS", "")

        admin_users = [u.strip().lower() for u in admin_users_str.split(",") if u.strip()]
        manager_users = [u.strip().lower() for u in manager_users_str.split(",") if u.strip()]

        email_lower = email.lower()

        if email_lower in admin_users:
            return "admin"
        elif email_lower in manager_users:
            return "manager"
        else:
            return "user"

    def has_permission(self, email: str, permission: str) -> bool:
        """
        Check if user has specific permission based on role.

        Args:
            email: User email address
            permission: Permission to check

        Returns:
            True if user has permission, False otherwise

        Permissions by role:
        - admin: All permissions
        - manager: view_all, view_analytics, manage_pilot (limited)
        - user: view_own
        """
        role = self.get_user_role(email)

        permissions = {
            "admin": [
                "view_all",
                "view_own",
                "manage_pilot",
                "edit_config",
                "view_analytics",
                "reprocess_meetings",
                "manage_users",
                "view_logs",
            ],
            "manager": ["view_all", "view_own", "view_analytics", "manage_pilot"],
            "user": ["view_own"],
        }

        role_permissions = permissions.get(role, [])
        return permission in role_permissions

    def require_permission(self, email: str, permission: str):
        """
        Require user to have specific permission.

        Args:
            email: User email address
            permission: Required permission

        Raises:
            UnauthorizedError: If user doesn't have permission
        """
        if not self.has_permission(email, permission):
            role = self.get_user_role(email)
            self.logger.warning(f"Permission denied for {email} (role: {role}): {permission}")
            raise UnauthorizedError(f"Permission denied: {permission}")

    def _validate_domain(self, email: str) -> bool:
        """
        Validate that email is from allowed domain.

        Currently hardcoded to townsquaremedia.com.
        In production, this could be configurable.

        Args:
            email: Email address to validate

        Returns:
            True if from allowed domain, False otherwise
        """
        return validate_domain(email, "townsquaremedia.com")

    def _generate_jwt(self, email: str, role: str) -> str:
        """
        Generate JWT token for user session.

        Args:
            email: User email
            role: User role

        Returns:
            JWT token string
        """
        payload = {
            "email": email,
            "role": role,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=self.session_timeout_hours),
        }

        token = jwt.encode(payload, self.jwt_secret, algorithm="HS256")
        return token

    def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired sessions from database.

        Returns:
            Number of sessions cleaned up
        """
        from src.core.database import UserSession

        db_session = self.db.get_session()
        try:
            deleted = (
                db_session.query(UserSession)
                .filter(UserSession.expires_at < datetime.utcnow(), UserSession.logout_at == None)
                .delete()
            )

            db_session.commit()

            if deleted > 0:
                self.logger.info(f"Cleaned up {deleted} expired sessions")

            return deleted

        except Exception as e:
            db_session.rollback()
            self.logger.error(f"Failed to cleanup sessions: {e}")
            return 0
        finally:
            db_session.close()


def get_auth_manager(database_manager: DatabaseManager, jwt_secret: Optional[str] = None) -> AuthManager:
    """
    Factory function to create AuthManager instance.

    Args:
        database_manager: Database manager instance
        jwt_secret: JWT secret key (loads from env if None)

    Returns:
        AuthManager instance
    """
    if jwt_secret is None:
        jwt_secret = os.getenv("JWT_SECRET_KEY")
        if not jwt_secret:
            # Generate temporary secret if not set
            import secrets

            jwt_secret = secrets.token_urlsafe(32)
            logging.warning("JWT_SECRET_KEY not set, using temporary key")

    return AuthManager(database_manager, jwt_secret)
