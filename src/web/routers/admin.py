"""
Admin routes for user management and system configuration.
"""

import html
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func

from ...core.database import DatabaseManager, UserPreference, EmailAlias, MeetingParticipant, Distribution, Meeting
from ...core.config import get_config
from ...graph.client import GraphAPIClient
from ...core.exceptions import GraphAPIError
from ...preferences.user_preferences import PreferenceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/web/templates")
config = get_config()
db = DatabaseManager(config.database.connection_string)


# =============================================================================
# Admin Notification Emails
# =============================================================================

def _get_system_description() -> str:
    """Return HTML description of the meeting summary system."""
    return """
    <div style="background: #f8f9fa; border-left: 4px solid #2563eb; padding: 15px; margin: 15px 0; border-radius: 4px;">
        <h3 style="margin-top: 0; color: #1e40af;">How This Works</h3>
        <p style="margin: 10px 0;">This is an automated meeting summary service that:</p>
        <ul style="margin: 10px 0; padding-left: 20px;">
            <li><strong>Monitors your Teams meetings</strong> that have transcription enabled</li>
            <li><strong>Generates AI-powered summaries</strong> using Claude, including action items, decisions, and key discussion points</li>
            <li><strong>Sends you an email summary</strong> after each meeting you attend</li>
        </ul>
        <p style="margin: 10px 0; font-size: 13px; color: #666;">
            Summaries are only generated for meetings where someone enables transcription.
            Your meeting content is processed securely and not stored beyond what's needed for the summary.
        </p>
    </div>
    """


def _send_admin_notification(
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str
) -> bool:
    """Send an admin notification email using Graph API."""
    try:
        graph_client = GraphAPIClient(config.graph_api)
        endpoint = f"/users/{config.app.email_from}/sendMail"

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": to_email,
                            "name": to_name or to_email
                        }
                    }
                ]
            }
        }

        graph_client.post(endpoint, json=payload)
        logger.info(f"Sent admin notification to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send admin notification to {to_email}: {e}")
        return False


def _send_admin_welcome_email(email: str, display_name: str = None):
    """Send welcome email when admin adds a user."""
    safe_name = html.escape(display_name) if display_name else 'there'
    unsubscribe_link = f"mailto:{config.app.email_from}?subject=unsubscribe"

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>You've Been Added to Meeting Summaries</h2>
        <p>Hi {safe_name},</p>
        <p>An administrator has subscribed you to the automated meeting summary service.</p>

        {_get_system_description()}

        <p>You'll start receiving summaries for any Teams meetings you attend that have transcription enabled.</p>

        <p style="margin: 20px 0;">
            <strong>Don't want to receive these emails?</strong> Click below to unsubscribe:
        </p>
        <p style="margin: 20px 0;">
            <a href="{unsubscribe_link}"
               style="display: inline-block; padding: 12px 24px; background-color: #6b7280; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: bold;">
                Unsubscribe
            </a>
        </p>
        <p style="font-size: 12px; color: #666;">
            Or reply to any summary email with "unsubscribe" in the subject line.
        </p>
        <p>Best regards,<br/>Meeting Notes Bot</p>
    </body>
    </html>
    """

    _send_admin_notification(email, display_name, "Meeting Summaries - You've Been Subscribed", body)


def _send_admin_unsubscribe_email(email: str, display_name: str = None, reason: str = "disabled"):
    """Send unsubscribe notification when admin disables or deletes a user."""
    safe_name = html.escape(display_name) if display_name else 'there'
    subscribe_link = f"mailto:{config.app.email_from}?subject=subscribe"

    if reason == "deleted":
        action_text = "removed you from"
    else:
        action_text = "disabled your subscription to"

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Unsubscribed from Meeting Summaries</h2>
        <p>Hi {safe_name},</p>
        <p>An administrator has {action_text} the automated meeting summary service.</p>
        <p>You will no longer receive AI-generated meeting summaries via email.</p>

        <p style="margin: 20px 0;">
            <strong>Want to resubscribe?</strong> Click below or contact your administrator:
        </p>
        <p style="margin: 20px 0;">
            <a href="{subscribe_link}"
               style="display: inline-block; padding: 12px 24px; background-color: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: bold;">
                Resubscribe
            </a>
        </p>
        <p>Best regards,<br/>Meeting Notes Bot</p>
    </body>
    </html>
    """

    _send_admin_notification(email, display_name, "Meeting Summaries - You've Been Unsubscribed", body)


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    days: Optional[int] = Query(default=30, description="Days to look back for counts")
):
    """Display user management page with meeting and summary counts."""
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

        # Calculate date cutoff for time-filtered counts
        cutoff_date = datetime.utcnow() - timedelta(days=days) if days else None

        # Get meeting attendance counts per email
        attendance_query = session.query(
            func.lower(MeetingParticipant.email).label('email'),
            func.count(MeetingParticipant.id).label('count')
        ).join(Meeting, MeetingParticipant.meeting_id == Meeting.id)

        if cutoff_date:
            attendance_query = attendance_query.filter(Meeting.start_time >= cutoff_date)

        attendance_counts = {
            row.email: row.count
            for row in attendance_query.group_by(func.lower(MeetingParticipant.email)).all()
        }

        # Get summary distribution counts per email (only sent)
        distribution_query = session.query(
            func.lower(Distribution.recipient).label('email'),
            func.count(Distribution.id).label('count')
        ).filter(Distribution.status == 'sent')

        if cutoff_date:
            distribution_query = distribution_query.filter(Distribution.sent_at >= cutoff_date)

        distribution_counts = {
            row.email: row.count
            for row in distribution_query.group_by(func.lower(Distribution.recipient)).all()
        }

        # Enhance users with alias info and counts
        enhanced_users = []
        for u in users:
            email_lower = u.user_email.lower()
            alias_info = alias_by_email.get(email_lower)

            # Get counts - check both user_email and primary_email
            meetings_attended = attendance_counts.get(email_lower, 0)
            summaries_received = distribution_counts.get(email_lower, 0)

            # Also check primary email for counts
            primary = alias_info.primary_email.lower() if alias_info and alias_info.primary_email else None
            if primary and primary != email_lower:
                meetings_attended += attendance_counts.get(primary, 0)
                summaries_received += distribution_counts.get(primary, 0)

            enhanced_users.append({
                "user_email": u.user_email,
                "receive_emails": u.receive_emails,
                "email_preference": u.email_preference,
                "updated_at": u.updated_at,
                "updated_by": u.updated_by,
                # Alias info
                "primary_email": alias_info.primary_email if alias_info else None,
                "display_name": alias_info.display_name if alias_info else None,
                "job_title": alias_info.job_title if alias_info else None,
                # Counts
                "meetings_attended": meetings_attended,
                "summaries_received": summaries_received,
            })

        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": {"email": "local", "role": "admin"},
                "users": enhanced_users,
                "total_users": len(users),
                "active_users": len([u for u in users if u.receive_emails]),
                "days_filter": days
            }
        )


@router.post("/users/add")
async def add_user(email: str = Form(...)):
    """Add a new user to the subscription list.

    Uses PreferenceManager to resolve email to GUID and create preference.
    Sends welcome email explaining the service.
    """
    email = email.strip().lower()

    if not email or '@' not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    # Use PreferenceManager to handle GUID resolution
    pref_manager = PreferenceManager(db)
    success = pref_manager.set_user_preference(
        email=email,
        receive_emails=True,
        updated_by="admin"
    )

    if not success:
        raise HTTPException(
            status_code=400,
            detail="Failed to add user. Could not resolve Azure AD identity."
        )

    # Get display name from email alias if available
    display_name = None
    with db.get_session() as session:
        alias = session.query(EmailAlias).filter_by(alias_email=email).first()
        if alias:
            display_name = alias.display_name

    # Send welcome email
    _send_admin_welcome_email(email, display_name)

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{email}/toggle")
async def toggle_user(email: str):
    """Toggle user's receive_emails status.

    Sends notification email when disabling (unsubscribe) or enabling (welcome).
    """
    email = email.lower().strip()

    with db.get_session() as session:
        # Try to find by user_id first (via EmailAlias lookup)
        alias = session.query(EmailAlias).filter_by(alias_email=email).first()
        user = None

        if alias and alias.user_id:
            user = session.query(UserPreference).filter_by(user_id=alias.user_id).first()

        # Fallback: try by email directly
        if not user:
            from sqlalchemy import func
            user = session.query(UserPreference).filter(
                func.lower(UserPreference.user_email) == email
            ).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Track previous state for notification
        was_subscribed = user.receive_emails
        user.receive_emails = not user.receive_emails
        session.commit()

        # Get display name for email
        display_name = alias.display_name if alias else None

        # Send appropriate notification
        if was_subscribed and not user.receive_emails:
            # User was disabled - send unsubscribe notification
            _send_admin_unsubscribe_email(email, display_name, reason="disabled")
        elif not was_subscribed and user.receive_emails:
            # User was re-enabled - send welcome email
            _send_admin_welcome_email(email, display_name)

        return {"success": True, "receive_emails": user.receive_emails}


@router.delete("/users/{email}")
async def delete_user(email: str):
    """Delete a user from the subscription list.

    Sends unsubscribe notification before deleting.
    """
    email = email.lower().strip()

    # Get display name before deletion for the notification email
    display_name = None
    with db.get_session() as session:
        alias = session.query(EmailAlias).filter_by(alias_email=email).first()
        if alias:
            display_name = alias.display_name

    # Use PreferenceManager for consistent GUID-based deletion
    pref_manager = PreferenceManager(db)
    success = pref_manager.delete_user_preference(email)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    # Send unsubscribe notification after successful deletion
    _send_admin_unsubscribe_email(email, display_name, reason="deleted")

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
