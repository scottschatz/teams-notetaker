"""
Dashboard Router

HTML pages for the web dashboard.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
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
            "user": {"email": "local", "role": "admin"},  # Dummy user for templates
            "page": "dashboard"
        }
    )


@router.get("/meetings", response_class=HTMLResponse)
async def meetings_page(request: Request):
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
            "user": {"email": "local", "role": "admin"},
            "page": "meetings"
        }
    )


@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
async def meeting_detail_page(
    request: Request,
    meeting_id: int
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
            "user": {"email": "local", "role": "admin"},
            "meeting_id": meeting_id,
            "page": "meetings"
        }
    )


@router.get("/pilot", response_class=HTMLResponse)
async def pilot_users_page(request: Request):
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
            "user": {"email": "local", "role": "admin"},
            "page": "pilot"
        }
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
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
            "user": {"email": "local", "role": "admin"},
            "page": "config"
        }
    )
