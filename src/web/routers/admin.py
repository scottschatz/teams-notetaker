"""
Admin routes for user management and system configuration.
"""

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc

from ...core.database import DatabaseManager, UserPreference, EmailAlias
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


@router.get("/email-aliases", response_class=HTMLResponse)
async def email_aliases_page(request: Request):
    """Display email aliases page showing all known email mappings."""
    with db.get_session() as session:
        # Get all aliases grouped by user_id
        all_aliases = session.query(EmailAlias).order_by(
            EmailAlias.user_id,
            desc(EmailAlias.last_used_at)
        ).all()

        # Group by user_id
        users_by_id = {}
        orphan_aliases = []

        for alias in all_aliases:
            if alias.user_id:
                if alias.user_id not in users_by_id:
                    users_by_id[alias.user_id] = {
                        'user_id': alias.user_id,
                        'display_name': alias.display_name,
                        'primary_email': alias.primary_email,
                        'aliases': []
                    }
                users_by_id[alias.user_id]['aliases'].append({
                    'email': alias.alias_email,
                    'is_primary': alias.alias_email == alias.primary_email,
                    'resolved_at': alias.resolved_at,
                    'last_used_at': alias.last_used_at
                })
            else:
                orphan_aliases.append({
                    'email': alias.alias_email,
                    'primary_email': alias.primary_email,
                    'display_name': alias.display_name,
                    'resolved_at': alias.resolved_at
                })

        return templates.TemplateResponse(
            "admin_email_aliases.html",
            {
                "request": request,
                "user": {"email": "local", "role": "admin"},
                "users": list(users_by_id.values()),
                "orphan_aliases": orphan_aliases,
                "total_aliases": len(all_aliases),
                "total_users": len(users_by_id)
            }
        )


@router.get("/api/email-aliases")
async def get_email_aliases():
    """API endpoint to get email aliases data."""
    with db.get_session() as session:
        aliases = session.query(EmailAlias).order_by(
            EmailAlias.primary_email
        ).all()

        return {
            "aliases": [
                {
                    "alias_email": a.alias_email,
                    "primary_email": a.primary_email,
                    "user_id": a.user_id,
                    "display_name": a.display_name,
                    "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                    "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None
                }
                for a in aliases
            ],
            "count": len(aliases)
        }
