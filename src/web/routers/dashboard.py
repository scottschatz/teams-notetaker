"""
Dashboard Router

HTML pages for the web dashboard.
"""

import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from ...auth.dependencies import get_current_user, require_admin


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Main dashboard page with overview stats.

    Returns:
        HTML dashboard page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": current_user,
            "page": "dashboard"
        }
    )


@router.get("/meetings", response_class=HTMLResponse)
async def meetings_page(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Meetings browser page.

    Returns:
        HTML meetings page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "meetings.html",
        {
            "request": request,
            "user": current_user,
            "page": "meetings"
        }
    )


@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
async def meeting_detail_page(
    request: Request,
    meeting_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Meeting detail page with transcript and summary.

    Args:
        meeting_id: Meeting ID

    Returns:
        HTML meeting detail page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "meeting_detail.html",
        {
            "request": request,
            "user": current_user,
            "meeting_id": meeting_id,
            "page": "meetings"
        }
    )


@router.get("/pilot", response_class=HTMLResponse)
async def pilot_users_page(
    request: Request,
    current_user: dict = Depends(require_admin)
):
    """
    Pilot users management page (admin only).

    Returns:
        HTML pilot users page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "pilot_users.html",
        {
            "request": request,
            "user": current_user,
            "page": "pilot"
        }
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page(
    request: Request,
    current_user: dict = Depends(require_admin)
):
    """
    Configuration editor page (admin only).

    Returns:
        HTML config page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "user": current_user,
            "page": "config"
        }
    )
