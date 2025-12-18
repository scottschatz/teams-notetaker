"""
Admin routes for user management and system configuration.
"""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc

from ...core.database import DatabaseManager, UserPreference
from ...core.config import get_config

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/web/templates")
config = get_config()
db = DatabaseManager(config.database.connection_string)


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Display user management page."""
    with db.get_session() as session:
        users = session.query(UserPreference).order_by(
            desc(UserPreference.updated_at)
        ).all()

        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": {"email": "local", "role": "admin"},
                "users": users,
                "total_users": len(users),
                "active_users": len([u for u in users if u.receive_emails])
            }
        )


@router.post("/users/add")
async def add_user(email: str = Form(...)):
    """Add a new user to the subscription list."""
    email = email.strip().lower()

    if not email or '@' not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    with db.get_session() as session:
        # Check if user already exists
        existing = session.query(UserPreference).filter_by(user_email=email).first()

        if existing:
            # Update to receive emails
            existing.receive_emails = True
            existing.email_preference = 'all'
            session.commit()
        else:
            # Create new user
            new_user = UserPreference(
                user_email=email,
                receive_emails=True,
                email_preference='all'
            )
            session.add(new_user)
            session.commit()

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{email}/toggle")
async def toggle_user(email: str):
    """Toggle user's receive_emails status."""
    with db.get_session() as session:
        user = session.query(UserPreference).filter_by(user_email=email).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.receive_emails = not user.receive_emails
        session.commit()

        return {"success": True, "receive_emails": user.receive_emails}


@router.delete("/users/{email}")
async def delete_user(email: str):
    """Delete a user from the subscription list."""
    with db.get_session() as session:
        user = session.query(UserPreference).filter_by(user_email=email).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        session.delete(user)
        session.commit()

        return {"success": True}


@router.post("/users/subscribe-all")
async def subscribe_all():
    """Enable email delivery for all users."""
    with db.get_session() as session:
        users = session.query(UserPreference).all()

        count = 0
        for user in users:
            if not user.receive_emails:
                user.receive_emails = True
                count += 1

        session.commit()

        return {
            "success": True,
            "updated": count,
            "message": f"Enabled email delivery for {count} users"
        }


@router.post("/users/unsubscribe-all")
async def unsubscribe_all():
    """Disable email delivery for all users."""
    with db.get_session() as session:
        users = session.query(UserPreference).all()

        count = 0
        for user in users:
            if user.receive_emails:
                user.receive_emails = False
                count += 1

        session.commit()

        return {
            "success": True,
            "updated": count,
            "message": f"Disabled email delivery for {count} users"
        }
