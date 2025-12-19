"""
Admin routes for user management and system configuration.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc

from ...core.database import DatabaseManager, UserPreference, EmailAlias
from ...core.config import get_config
from ...graph.client import GraphAPIClient
from ...core.exceptions import GraphAPIError

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

        # Get all email aliases for lookup
        all_aliases = session.query(EmailAlias).all()

        # Build lookup: email -> alias info (by user_id for grouping)
        alias_by_email = {}
        user_id_by_email = {}
        for alias in all_aliases:
            alias_by_email[alias.alias_email.lower()] = alias
            if alias.user_id:
                user_id_by_email[alias.alias_email.lower()] = alias.user_id
                if alias.primary_email:
                    user_id_by_email[alias.primary_email.lower()] = alias.user_id

        # Enhance users with alias info
        enhanced_users = []
        for u in users:
            email_lower = u.user_email.lower()
            alias_info = alias_by_email.get(email_lower)
            enhanced_users.append({
                "user_email": u.user_email,
                "receive_emails": u.receive_emails,
                "email_preference": u.email_preference,
                "updated_at": u.updated_at,
                "updated_by": u.updated_by,
                # Alias info
                "primary_email": alias_info.primary_email if alias_info else None,
                "user_id": alias_info.user_id if alias_info else user_id_by_email.get(email_lower),
                "display_name": alias_info.display_name if alias_info else None,
                "job_title": alias_info.job_title if alias_info else None,
            })

        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": {"email": "local", "role": "admin"},
                "users": enhanced_users,
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


@router.post("/users/{email}/refresh")
async def refresh_user_info(email: str):
    """Refresh user info from Azure AD (GUID, primary email, job title, etc.)."""
    try:
        graph_client = GraphAPIClient(config.graph_api)

        # Look up user in Azure AD
        user_info = graph_client.get(
            f"/users/{email}",
            params={"$select": "id,mail,userPrincipalName,displayName,jobTitle"}
        )

        user_id = user_info.get("id")
        primary_email = user_info.get("mail") or user_info.get("userPrincipalName", "")
        primary_email = primary_email.lower().strip() if primary_email else email.lower()
        display_name = user_info.get("displayName") or ""
        job_title = user_info.get("jobTitle") or ""

        # Update or create EmailAlias record
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with db.get_session() as session:
            alias_record = EmailAlias(
                alias_email=email.lower(),
                primary_email=primary_email,
                user_id=user_id,
                display_name=display_name,
                job_title=job_title,
                resolved_at=now,
                last_used_at=now
            )
            session.merge(alias_record)

            # If primary email is different, also store that mapping
            if primary_email and primary_email != email.lower():
                primary_record = EmailAlias(
                    alias_email=primary_email,
                    primary_email=primary_email,
                    user_id=user_id,
                    display_name=display_name,
                    job_title=job_title,
                    resolved_at=now,
                    last_used_at=now
                )
                session.merge(primary_record)

            session.commit()

        return {
            "success": True,
            "user_id": user_id,
            "primary_email": primary_email,
            "display_name": display_name,
            "job_title": job_title,
            "message": f"Refreshed info for {email}"
        }

    except GraphAPIError as e:
        raise HTTPException(status_code=404, detail=f"User not found in Azure AD: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh user info: {e}")


@router.post("/users/refresh-all")
async def refresh_all_users():
    """Refresh Azure AD info for all users."""
    graph_client = GraphAPIClient(config.graph_api)

    with db.get_session() as session:
        users = session.query(UserPreference).all()
        updated = 0
        errors = []

        for user in users:
            try:
                user_info = graph_client.get(
                    f"/users/{user.user_email}",
                    params={"$select": "id,mail,userPrincipalName,displayName,jobTitle"}
                )

                user_id = user_info.get("id")
                primary_email = user_info.get("mail") or user_info.get("userPrincipalName", "")
                primary_email = primary_email.lower().strip() if primary_email else user.user_email.lower()
                display_name = user_info.get("displayName") or ""
                job_title = user_info.get("jobTitle") or ""

                now = datetime.now(timezone.utc).replace(tzinfo=None)
                alias_record = EmailAlias(
                    alias_email=user.user_email.lower(),
                    primary_email=primary_email,
                    user_id=user_id,
                    display_name=display_name,
                    job_title=job_title,
                    resolved_at=now,
                    last_used_at=now
                )
                session.merge(alias_record)

                # If primary email is different, also store that mapping
                if primary_email and primary_email != user.user_email.lower():
                    primary_record = EmailAlias(
                        alias_email=primary_email,
                        primary_email=primary_email,
                        user_id=user_id,
                        display_name=display_name,
                        job_title=job_title,
                        resolved_at=now,
                        last_used_at=now
                    )
                    session.merge(primary_record)

                updated += 1
            except Exception as e:
                errors.append(f"{user.user_email}: {e}")

        session.commit()

    return {
        "success": True,
        "updated": updated,
        "errors": errors,
        "message": f"Refreshed {updated} users" + (f", {len(errors)} errors" if errors else "")
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
