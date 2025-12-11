"""
Authentication Router

Handles login, logout, and Azure AD SSO callback.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ...auth.auth_manager import AuthManager
from ...auth.auth_sso import AzureADAuth
from ...auth.dependencies import get_auth_manager, get_db
from ...core.database import DatabaseManager
from ...core.config import get_config
from ...core.exceptions import AuthenticationError


logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    """Login request body."""
    email: str
    password: Optional[str] = None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Display login page.

    Returns:
        HTML login page
    """
    templates = request.app.state.templates
    config = get_config()

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "azure_ad_enabled": config.azure_ad.enabled
        }
    )


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    login_data: LoginRequest,
    auth: AuthManager = Depends(get_auth_manager)
):
    """
    Handle password login.

    Args:
        request: FastAPI request
        response: FastAPI response
        login_data: Login credentials
        auth: AuthManager instance

    Returns:
        JSON response with redirect URL
    """
    try:
        # Get client IP
        ip_address = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")

        # Attempt login
        session_info = auth.login(
            email=login_data.email,
            password=login_data.password,
            ip_address=ip_address,
            user_agent=user_agent
        )

        # Set session cookie
        response.set_cookie(
            key="session_token",
            value=session_info["session_token"],
            httponly=True,
            max_age=8 * 3600,  # 8 hours
            samesite="lax"
        )

        logger.info(f"✓ User logged in: {login_data.email}")

        return {
            "success": True,
            "message": "Login successful",
            "redirect_url": "/dashboard"
        }

    except AuthenticationError as e:
        logger.warning(f"Login failed for {login_data.email}: {e}")
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/sso")
async def sso_login(
    request: Request,
    db: DatabaseManager = Depends(get_db)
):
    """
    Initiate Azure AD SSO login.

    Returns:
        Redirect to Microsoft login page
    """
    config = get_config()

    if not config.azure_ad.enabled:
        raise HTTPException(status_code=400, detail="SSO not enabled")

    try:
        # Initialize Azure AD auth
        azure_auth = AzureADAuth(config.azure_ad, db)

        # Get client IP
        ip_address = request.client.host if request.client else None

        # Get auth URL
        redirect_uri = str(request.url_for("sso_callback"))
        auth_url, state = azure_auth.get_auth_url(redirect_uri, ip_address)

        logger.info(f"SSO login initiated (state: {state[:8]}..., ip: {ip_address})")

        # Redirect to Microsoft
        return RedirectResponse(url=auth_url)

    except Exception as e:
        logger.error(f"SSO login failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"SSO login failed: {e}")


@router.get("/callback")
async def sso_callback(
    request: Request,
    response: Response,
    db: DatabaseManager = Depends(get_db),
    auth: AuthManager = Depends(get_auth_manager)
):
    """
    Handle Azure AD SSO callback.

    Returns:
        Redirect to dashboard on success
    """
    config = get_config()

    if not config.azure_ad.enabled:
        raise HTTPException(status_code=400, detail="SSO not enabled")

    try:
        # Initialize Azure AD auth
        azure_auth = AzureADAuth(config.azure_ad, db)

        # Get auth response from query params
        auth_response = dict(request.query_params)

        # Check for errors
        if "error" in auth_response:
            error_desc = auth_response.get("error_description", auth_response["error"])
            logger.error(f"SSO callback error: {error_desc}")
            raise HTTPException(status_code=400, detail=f"SSO failed: {error_desc}")

        # Exchange code for token
        token_result = azure_auth.acquire_token_by_auth_code(auth_response)

        # Get user info
        access_token = token_result["access_token"]
        user_info = azure_auth.get_user_info(access_token)

        email = user_info["email"]
        display_name = user_info.get("name", email)

        # Validate user
        is_valid, error = azure_auth.validate_user(email)
        if not is_valid:
            logger.warning(f"SSO user validation failed: {error}")
            raise HTTPException(status_code=403, detail=error)

        # Create session
        ip_address = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")

        session_info = auth.login(
            email=email,
            password=None,  # SSO login, no password
            ip_address=ip_address,
            user_agent=user_agent
        )

        # Set session cookie
        response.set_cookie(
            key="session_token",
            value=session_info["session_token"],
            httponly=True,
            max_age=8 * 3600,  # 8 hours
            samesite="lax"
        )

        logger.info(f"✓ SSO login successful: {email}")

        # Redirect to dashboard
        return RedirectResponse(url="/dashboard")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSO callback failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"SSO callback failed: {e}")


@router.post("/logout")
async def logout(
    response: Response,
    session_token: str = Cookie(None, alias="session_token"),
    auth: AuthManager = Depends(get_auth_manager)
):
    """
    Handle logout.

    Returns:
        JSON response with redirect URL
    """
    if session_token:
        try:
            auth.logout(session_token)
            logger.info("User logged out")
        except Exception as e:
            logger.error(f"Logout error: {e}")

    # Clear session cookie
    response.delete_cookie(key="session_token")

    return {
        "success": True,
        "message": "Logged out successfully",
        "redirect_url": "/auth/login"
    }
