"""
Azure AD SSO Authentication

Implements Single Sign-On using Microsoft Authentication Library (MSAL).
Adapted from invoice-bot patterns for FastAPI.
"""

import logging
from typing import Optional, Dict, Tuple
import secrets
from datetime import datetime, timedelta
from msal import ConfidentialClientApplication

from ..core.config import AzureADConfig
from ..core.database import DatabaseManager, AuthFlow, UserSession
from ..core.exceptions import AuthenticationError
from ..utils.validators import validate_email, validate_domain


logger = logging.getLogger(__name__)


class AzureADAuth:
    """
    Manages Azure AD SSO authentication for FastAPI.

    Features:
    - OAuth 2.0 authorization code flow
    - Auth flow persistence in database (survives session loss)
    - State parameter for CSRF protection
    - One-time use auth flows (10-minute expiration)
    - Domain validation (@townsquaremedia.com)

    Usage:
        config = AzureADConfig(...)
        db = DatabaseManager(...)
        auth = AzureADAuth(config, db)

        # Step 1: Get auth URL
        auth_url, state = auth.get_auth_url("http://localhost:8000/auth/callback")

        # Step 2: User redirects to Microsoft, then back with auth code
        # Step 3: Exchange code for token
        result = auth.acquire_token_by_auth_code(auth_response)
        user_info = auth.get_user_info(result["access_token"])
    """

    def __init__(self, config: AzureADConfig, db: DatabaseManager):
        """
        Initialize Azure AD authentication.

        Args:
            config: AzureADConfig with client credentials
            db: DatabaseManager for auth flow persistence
        """
        self.config = config
        self.db = db
        self.enabled = config.enabled

        if not self.enabled:
            logger.info("Azure AD SSO is disabled")
            return

        # Initialize MSAL confidential client
        self.app = ConfidentialClientApplication(
            client_id=config.client_id,
            client_credential=config.client_secret,
            authority=config.authority
        )

        logger.info(f"AzureADAuth initialized (tenant: {config.tenant_id[:8]}..., enabled: {self.enabled})")

        # Clean up expired auth flows
        try:
            expired_count = self._cleanup_expired_flows()
            logger.info(f"Cleaned up {expired_count} expired auth flows")
        except Exception as e:
            logger.warning(f"Failed to clean up expired flows: {e}")

    def is_enabled(self) -> bool:
        """Check if SSO is enabled."""
        return self.enabled

    def get_auth_url(self, redirect_uri: str, ip_address: Optional[str] = None) -> Tuple[str, str]:
        """
        Get authorization URL for user to login.

        Args:
            redirect_uri: Redirect URI after authentication
            ip_address: Client IP address (for security tracking)

        Returns:
            Tuple of (auth_url: str, state: str)

        Raises:
            AuthenticationError: If auth URL generation fails
        """
        if not self.enabled:
            raise AuthenticationError("SSO not enabled")

        try:
            # Generate state parameter (CSRF protection)
            state = secrets.token_urlsafe(32)

            # Initiate auth code flow
            flow = self.app.initiate_auth_code_flow(
                scopes=self.config.scopes,
                redirect_uri=redirect_uri,
                state=state
            )

            if "error" in flow:
                raise AuthenticationError(f"Failed to initiate auth flow: {flow.get('error_description', flow['error'])}")

            auth_url = flow.get("auth_uri")
            if not auth_url:
                raise AuthenticationError("No auth URI in flow")

            # Save flow to database (survives session loss)
            with self.db.get_session() as session:
                auth_flow = AuthFlow(
                    state=state,
                    flow_data=flow,  # Store entire flow as JSON
                    expires_at=datetime.now() + timedelta(minutes=10),
                    ip_address=ip_address
                )
                session.add(auth_flow)
                session.commit()

            logger.info(f"Auth flow created (state: {state[:8]}..., ip: {ip_address})")

            return auth_url, state

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"Failed to get auth URL: {e}", exc_info=True)
            raise AuthenticationError(f"Auth URL generation failed: {e}")

    def acquire_token_by_auth_code(self, auth_response: Dict) -> Dict:
        """
        Exchange authorization code for access token.

        Args:
            auth_response: Response from Microsoft with:
                - code: Authorization code
                - state: State parameter (for verification)

        Returns:
            Token response dictionary with:
                - access_token: Access token
                - token_type: Token type (Bearer)
                - expires_in: Expiration time
                - refresh_token: Refresh token (if available)

        Raises:
            AuthenticationError: If token exchange fails
        """
        if not self.enabled:
            raise AuthenticationError("SSO not enabled")

        try:
            state = auth_response.get("state")
            if not state:
                raise AuthenticationError("No state parameter in auth response")

            # Load flow from database
            with self.db.get_session() as session:
                auth_flow = session.query(AuthFlow).filter_by(state=state).first()

                if not auth_flow:
                    raise AuthenticationError("Invalid or expired auth state")

                if auth_flow.used:
                    raise AuthenticationError("Auth code already used (replay attack?)")

                if auth_flow.expires_at < datetime.now():
                    raise AuthenticationError("Auth flow expired")

                # Mark as used (one-time use)
                auth_flow.used = True

                flow_data = auth_flow.flow_data

                session.commit()

            logger.info(f"Retrieved auth flow (state: {state[:8]}...)")

            # Acquire token using authorization code
            result = self.app.acquire_token_by_auth_code(
                code=auth_response,
                scopes=self.config.scopes
            )

            if "error" in result:
                error_desc = result.get("error_description", result["error"])
                raise AuthenticationError(f"Token acquisition failed: {error_desc}")

            if "access_token" not in result:
                raise AuthenticationError("No access token in response")

            logger.info("✓ Access token acquired successfully")

            return result

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"Failed to acquire token: {e}", exc_info=True)
            raise AuthenticationError(f"Token acquisition failed: {e}")

    def get_user_info(self, access_token: str) -> Dict:
        """
        Get user information from Microsoft Graph.

        Args:
            access_token: Access token

        Returns:
            User info dictionary with:
                - email: User email
                - name: Display name
                - given_name: First name
                - family_name: Last name

        Raises:
            AuthenticationError: If user info request fails
        """
        try:
            import requests

            # Call Graph API to get user info
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                raise AuthenticationError(f"Failed to get user info: {response.status_code}")

            user_data = response.json()

            # Extract user info
            email = user_data.get("mail") or user_data.get("userPrincipalName", "")
            name = user_data.get("displayName", "")
            given_name = user_data.get("givenName", "")
            family_name = user_data.get("surname", "")

            logger.info(f"Retrieved user info for {email}")

            return {
                "email": email.lower(),
                "name": name,
                "given_name": given_name,
                "family_name": family_name
            }

        except requests.RequestException as e:
            logger.error(f"Failed to get user info: {e}")
            raise AuthenticationError(f"User info request failed: {e}")

    def validate_user(self, email: str) -> Tuple[bool, str]:
        """
        Validate user email against allowed domain.

        Args:
            email: User email address

        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        # Validate email format
        is_valid, error = validate_email(email)
        if not is_valid:
            return False, f"Invalid email format: {error}"

        # Validate domain
        is_valid, error = validate_domain(email, self.config.allowed_domain)
        if not is_valid:
            return False, f"Domain not allowed: {error}"

        return True, ""

    def _cleanup_expired_flows(self) -> int:
        """
        Delete expired auth flows from database.

        Returns:
            Number of flows deleted
        """
        with self.db.get_session() as session:
            deleted = session.query(AuthFlow).filter(
                AuthFlow.expires_at < datetime.now()
            ).delete()
            session.commit()
            return deleted
