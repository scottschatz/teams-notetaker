"""
Analytics routes for reporting and insights.
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, case, distinct, extract

from ...core.database import DatabaseManager, Meeting, Summary, Distribution, MeetingParticipant, UserPreference
from ...core.config import get_config

router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory="src/web/templates")
config = get_config()
db = DatabaseManager(config.database.connection_string)


def get_date_range(days: int):
    """Get start date for the given number of days back."""
    if days == 0:
        return None  # All time
    return datetime.utcnow() - timedelta(days=days)


def get_prev_period_range(days: int):
    """Get start/end dates for the previous comparison period."""
    if days == 0:
        return None, None
    end = datetime.utcnow() - timedelta(days=days)
    start = end - timedelta(days=days)
    return start, end


@router.get("", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Display analytics dashboard page."""
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "user": {"email": "local", "role": "admin"},
            "page": "analytics"
        }
    )


@router.get("/api/overview")
async def get_overview(days: int = Query(default=30, description="Number of days to look back")):
    """Get KPI overview data for dashboard cards."""
    start_date = get_date_range(days)
    prev_start, prev_end = get_prev_period_range(days)

    with db.get_session() as session:
        # Current period meetings
        meetings_query = session.query(Meeting)
        if start_date:
            meetings_query = meetings_query.filter(Meeting.start_time >= start_date)

        total_meetings = meetings_query.count()
        meetings_with_transcript = meetings_query.filter(Meeting.has_transcript == True).count()

        # Previous period meetings
        prev_meetings = 0
        if prev_start and prev_end:
            prev_meetings = session.query(Meeting).filter(
                Meeting.start_time >= prev_start,
                Meeting.start_time < prev_end
            ).count()

        # Current period summaries
        summaries_query = session.query(Summary)
        if start_date:
            summaries_query = summaries_query.filter(Summary.generated_at >= start_date)

        total_summaries = summaries_query.count()

        # Previous period summaries
        prev_summaries = 0
        if prev_start and prev_end:
            prev_summaries = session.query(Summary).filter(
                Summary.generated_at >= prev_start,
                Summary.generated_at < prev_end
            ).count()

        # Success rate (completed vs total meetings in period)
        completed_meetings = meetings_query.filter(Meeting.status == 'completed').count()
        success_rate = (completed_meetings / total_meetings * 100) if total_meetings > 0 else 0

        # Current period emails
        emails_query = session.query(Distribution).filter(Distribution.distribution_type == 'email')
        if start_date:
            emails_query = emails_query.filter(Distribution.sent_at >= start_date)

        total_emails = emails_query.filter(Distribution.status == 'sent').count()
        unique_recipients = session.query(distinct(Distribution.recipient)).filter(
            Distribution.distribution_type == 'email',
            Distribution.status == 'sent'
        )
        if start_date:
            unique_recipients = unique_recipients.filter(Distribution.sent_at >= start_date)
        unique_recipients_count = unique_recipients.count()

        # Previous period emails
        prev_emails = 0
        if prev_start and prev_end:
            prev_emails = session.query(Distribution).filter(
                Distribution.distribution_type == 'email',
                Distribution.status == 'sent',
                Distribution.sent_at >= prev_start,
                Distribution.sent_at < prev_end
            ).count()

        # AI Cost calculation
        cost_query = session.query(
            func.sum(Summary.prompt_tokens).label('input_tokens'),
            func.sum(Summary.completion_tokens).label('output_tokens'),
            Summary.model
        ).group_by(Summary.model)

        if start_date:
            cost_query = cost_query.filter(Summary.generated_at >= start_date)

        total_cost = 0.0
        for row in cost_query.all():
            input_tokens = row.input_tokens or 0
            output_tokens = row.output_tokens or 0
            total_cost += calculate_cost(row.model, input_tokens, output_tokens)

        avg_cost = total_cost / total_summaries if total_summaries > 0 else 0

        # Previous period cost
        prev_cost = 0.0
        if prev_start and prev_end:
            prev_cost_query = session.query(
                func.sum(Summary.prompt_tokens).label('input_tokens'),
                func.sum(Summary.completion_tokens).label('output_tokens'),
                Summary.model
            ).filter(
                Summary.generated_at >= prev_start,
                Summary.generated_at < prev_end
            ).group_by(Summary.model)

            for row in prev_cost_query.all():
                input_tokens = row.input_tokens or 0
                output_tokens = row.output_tokens or 0
                prev_cost += calculate_cost(row.model, input_tokens, output_tokens)

        return {
            "meetings": {
                "total": total_meetings,
                "with_transcript": meetings_with_transcript,
                "transcript_rate": round(meetings_with_transcript / total_meetings * 100, 1) if total_meetings > 0 else 0,
                "prev_period": prev_meetings,
                "change": round((total_meetings - prev_meetings) / prev_meetings * 100, 1) if prev_meetings > 0 else 0
            },
            "summaries": {
                "total": total_summaries,
                "success_rate": round(success_rate, 1),
                "prev_period": prev_summaries,
                "change": round((total_summaries - prev_summaries) / prev_summaries * 100, 1) if prev_summaries > 0 else 0
            },
            "emails": {
                "total": total_emails,
                "unique_recipients": unique_recipients_count,
                "prev_period": prev_emails,
                "change": round((total_emails - prev_emails) / prev_emails * 100, 1) if prev_emails > 0 else 0
            },
            "ai_cost": {
                "total": round(total_cost, 2),
                "avg_per_meeting": round(avg_cost, 4),
                "prev_period": round(prev_cost, 2),
                "change": round((total_cost - prev_cost) / prev_cost * 100, 1) if prev_cost > 0 else 0
            }
        }


@router.get("/api/meeting-volume")
async def get_meeting_volume(days: int = Query(default=30)):
    """Get daily meeting counts for line chart."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            func.date(Meeting.start_time).label('date'),
            func.count(Meeting.id).label('total'),
            func.sum(case((Meeting.status == 'completed', 1), else_=0)).label('completed'),
            func.sum(case((Meeting.status == 'failed', 1), else_=0)).label('failed'),
            func.sum(case((Meeting.status.in_(['skipped', 'no_transcript']), 1), else_=0)).label('skipped')
        ).group_by(func.date(Meeting.start_time)).order_by(func.date(Meeting.start_time))

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        results = query.all()

        return {
            "labels": [str(r.date) for r in results],
            "datasets": {
                "total": [r.total for r in results],
                "completed": [r.completed for r in results],
                "failed": [r.failed for r in results],
                "skipped": [r.skipped for r in results]
            }
        }


@router.get("/api/pipeline-funnel")
async def get_pipeline_funnel(days: int = Query(default=30)):
    """Get processing pipeline stage counts."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Meeting)
        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        total = query.count()
        has_transcript = query.filter(Meeting.has_transcript == True).count()
        has_summary = query.filter(Meeting.has_summary == True).count()
        has_distribution = query.filter(Meeting.has_distribution == True).count()

        return {
            "stages": [
                {"name": "Discovered", "count": total, "percent": 100},
                {"name": "Has Transcript", "count": has_transcript, "percent": round(has_transcript / total * 100, 1) if total > 0 else 0},
                {"name": "Has Summary", "count": has_summary, "percent": round(has_summary / total * 100, 1) if total > 0 else 0},
                {"name": "Distributed", "count": has_distribution, "percent": round(has_distribution / total * 100, 1) if total > 0 else 0}
            ]
        }


@router.get("/api/top-organizers")
async def get_top_organizers(days: int = Query(default=30), limit: int = Query(default=10)):
    """Get top meeting organizers."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            Meeting.organizer_name,
            Meeting.organizer_email,
            func.count(Meeting.id).label('meeting_count'),
            func.sum(func.coalesce(Meeting.participant_count, 0)).label('total_participants'),
            func.avg(Meeting.duration_minutes).label('avg_duration')
        ).filter(Meeting.status == 'completed')

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        query = query.group_by(
            Meeting.organizer_name, Meeting.organizer_email
        ).order_by(func.count(Meeting.id).desc()).limit(limit)

        results = query.all()

        return {
            "organizers": [
                {
                    "name": r.organizer_name or r.organizer_email or "Unknown",
                    "email": r.organizer_email,
                    "meetings": r.meeting_count,
                    "participants": int(r.total_participants or 0),
                    "avg_duration": round(r.avg_duration or 0, 0)
                }
                for r in results
            ]
        }


@router.get("/api/duration-distribution")
async def get_duration_distribution(days: int = Query(default=30)):
    """Get meeting duration distribution for histogram."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Meeting.duration_minutes).filter(
            Meeting.duration_minutes.isnot(None),
            Meeting.status == 'completed'
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        durations = [r[0] for r in query.all()]

        # Bucket the durations
        buckets = {
            "<15 min": 0,
            "15-30 min": 0,
            "30-60 min": 0,
            "1-2 hours": 0,
            "2+ hours": 0
        }

        for d in durations:
            if d < 15:
                buckets["<15 min"] += 1
            elif d < 30:
                buckets["15-30 min"] += 1
            elif d < 60:
                buckets["30-60 min"] += 1
            elif d < 120:
                buckets["1-2 hours"] += 1
            else:
                buckets["2+ hours"] += 1

        return {
            "labels": list(buckets.keys()),
            "data": list(buckets.values())
        }


@router.get("/api/ai-costs")
async def get_ai_costs(days: int = Query(default=30)):
    """Get AI cost breakdown by day and model."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            func.date(Summary.generated_at).label('date'),
            Summary.model,
            func.sum(Summary.prompt_tokens).label('input_tokens'),
            func.sum(Summary.completion_tokens).label('output_tokens'),
            func.count(Summary.id).label('count')
        ).group_by(
            func.date(Summary.generated_at), Summary.model
        ).order_by(func.date(Summary.generated_at))

        if start_date:
            query = query.filter(Summary.generated_at >= start_date)

        results = query.all()

        # Organize by date
        dates = {}
        for r in results:
            date_str = str(r.date)
            if date_str not in dates:
                dates[date_str] = {"haiku": 0, "sonnet": 0, "opus": 0, "other": 0}

            input_tokens = r.input_tokens or 0
            output_tokens = r.output_tokens or 0
            model = (r.model or '').lower()
            cost = calculate_cost(r.model, input_tokens, output_tokens)

            if 'haiku' in model:
                dates[date_str]["haiku"] += cost
            elif 'sonnet' in model:
                dates[date_str]["sonnet"] += cost
            elif 'opus' in model:
                dates[date_str]["opus"] += cost
            else:
                dates[date_str]["other"] += cost

        sorted_dates = sorted(dates.keys())

        return {
            "labels": sorted_dates,
            "datasets": {
                "haiku": [round(dates[d]["haiku"], 4) for d in sorted_dates],
                "sonnet": [round(dates[d]["sonnet"], 4) for d in sorted_dates],
                "opus": [round(dates[d]["opus"], 4) for d in sorted_dates],
                "other": [round(dates[d]["other"], 4) for d in sorted_dates]
            }
        }


@router.get("/api/meeting-heatmap")
async def get_meeting_heatmap(days: int = Query(default=30)):
    """Get meeting time heatmap data (hour x day of week)."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            extract('dow', Meeting.start_time).label('day_of_week'),
            extract('hour', Meeting.start_time).label('hour'),
            func.count(Meeting.id).label('count')
        ).filter(Meeting.status == 'completed').group_by(
            extract('dow', Meeting.start_time),
            extract('hour', Meeting.start_time)
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        results = query.all()

        # Build matrix (days x hours)
        days_of_week = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        hours = list(range(7, 20))  # 7am to 7pm

        matrix = [[0 for _ in hours] for _ in days_of_week]
        max_count = 0

        for r in results:
            dow = int(r.day_of_week)
            hour = int(r.hour)
            count = r.count

            if 7 <= hour <= 19:
                hour_idx = hour - 7
                matrix[dow][hour_idx] = count
                max_count = max(max_count, count)

        return {
            "days": days_of_week,
            "hours": [f"{h}:00" for h in hours],
            "data": matrix,
            "max": max_count
        }


@router.get("/api/top-users")
async def get_top_users(days: int = Query(default=30), limit: int = Query(default=20)):
    """Get top users by meeting attendance."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        # Get participant stats
        query = session.query(
            MeetingParticipant.email,
            MeetingParticipant.display_name,
            func.count(distinct(MeetingParticipant.meeting_id)).label('meetings_attended')
        ).join(Meeting).filter(
            MeetingParticipant.attended == True
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        query = query.group_by(
            MeetingParticipant.email, MeetingParticipant.display_name
        ).order_by(func.count(distinct(MeetingParticipant.meeting_id)).desc()).limit(limit)

        participants = query.all()

        # Get organized meeting counts
        org_counts = {}
        org_query = session.query(
            Meeting.organizer_email,
            func.count(Meeting.id).label('organized')
        ).group_by(Meeting.organizer_email)

        if start_date:
            org_query = org_query.filter(Meeting.start_time >= start_date)

        for r in org_query.all():
            if r.organizer_email:
                org_counts[r.organizer_email.lower()] = r.organized

        # Get email distribution counts
        email_counts = {}
        dist_query = session.query(
            func.lower(Distribution.recipient).label('email'),
            func.count(Distribution.id).label('emails')
        ).filter(
            Distribution.distribution_type == 'email',
            Distribution.status == 'sent'
        ).group_by(func.lower(Distribution.recipient))

        if start_date:
            dist_query = dist_query.filter(Distribution.sent_at >= start_date)

        for r in dist_query.all():
            email_counts[r.email] = r.emails

        # Get last meeting date per user
        last_meeting = {}
        last_query = session.query(
            MeetingParticipant.email,
            func.max(Meeting.start_time).label('last_meeting')
        ).join(Meeting).filter(
            MeetingParticipant.attended == True
        ).group_by(MeetingParticipant.email)

        if start_date:
            last_query = last_query.filter(Meeting.start_time >= start_date)

        for r in last_query.all():
            if r.email:
                last_meeting[r.email.lower()] = r.last_meeting

        return {
            "users": [
                {
                    "name": p.display_name or p.email,
                    "email": p.email,
                    "meetings_attended": p.meetings_attended,
                    "meetings_organized": org_counts.get(p.email.lower(), 0) if p.email else 0,
                    "summaries_received": email_counts.get(p.email.lower(), 0) if p.email else 0,
                    "last_meeting": last_meeting.get(p.email.lower()).isoformat() if p.email and last_meeting.get(p.email.lower()) else None
                }
                for p in participants
            ]
        }


@router.get("/api/ai-processing")
async def get_ai_processing(days: int = Query(default=30), limit: int = Query(default=50)):
    """Get AI processing details for table view."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            Summary.id,
            Summary.meeting_id,
            Summary.generated_at,
            Summary.model,
            Summary.prompt_tokens,
            Summary.completion_tokens,
            Summary.generation_time_ms,
            Meeting.subject
        ).join(Meeting)

        if start_date:
            query = query.filter(Summary.generated_at >= start_date)

        query = query.order_by(Summary.generated_at.desc()).limit(limit)

        results = query.all()

        return {
            "summaries": [
                {
                    "id": r.id,
                    "meeting_id": r.meeting_id,
                    "date": r.generated_at.isoformat() if r.generated_at else None,
                    "subject": r.subject,
                    "model": r.model,
                    "input_tokens": r.prompt_tokens or 0,
                    "output_tokens": r.completion_tokens or 0,
                    "total_tokens": (r.prompt_tokens or 0) + (r.completion_tokens or 0),
                    "cost": calculate_cost(r.model, r.prompt_tokens, r.completion_tokens),
                    "gen_time_ms": r.generation_time_ms
                }
                for r in results
            ]
        }


@router.get("/api/recent-activity")
async def get_recent_activity(days: int = Query(default=30), limit: int = Query(default=50)):
    """Get recent meeting activity for table view."""
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Meeting).filter(
            Meeting.status.in_(['completed', 'failed', 'no_transcript'])
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        query = query.order_by(Meeting.discovered_at.desc()).limit(limit)

        results = query.all()

        return {
            "meetings": [
                {
                    "id": m.id,
                    "subject": m.subject,
                    "organizer": m.organizer_name or m.organizer_email,
                    "start_time": m.start_time.isoformat() if m.start_time else None,
                    "discovered_at": m.discovered_at.isoformat() if m.discovered_at else None,
                    "status": m.status,
                    "duration_minutes": m.duration_minutes,
                    "participant_count": m.participant_count
                }
                for m in results
            ]
        }


# Model pricing (per million tokens) - keep in sync with ClaudeClient.MODEL_PRICING
# Note: Historical multi-stage summaries may have incomplete token counts
MODEL_PRICING = {
    "haiku": {"input": 1.00, "output": 5.00},      # Claude 3.5/4.5 Haiku
    "sonnet": {"input": 3.00, "output": 15.00},    # Claude 3.5/4.5 Sonnet
    "opus": {"input": 15.00, "output": 75.00},     # Claude 3/4.5 Opus
}


def get_model_pricing(model: str) -> dict:
    """Get pricing for a model based on model name pattern matching."""
    model_lower = (model or '').lower()
    if 'haiku' in model_lower:
        return MODEL_PRICING["haiku"]
    elif 'sonnet' in model_lower:
        return MODEL_PRICING["sonnet"]
    elif 'opus' in model_lower:
        return MODEL_PRICING["opus"]
    else:
        # Default to Sonnet pricing for unknown models
        return MODEL_PRICING["sonnet"]


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate AI cost based on model and token usage."""
    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0

    pricing = get_model_pricing(model)
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 4)


# =============================================================================
# ENTERPRISE INTELLIGENCE DASHBOARD ENDPOINTS
# =============================================================================

@router.get("/api/dashboard/status")
async def get_dashboard_status():
    """
    Get system health status for dashboard.

    Returns:
        - webhook status
        - worker status
        - queue depth
        - last meeting processed
        - errors today
    """
    from ...core.database import JobQueue, SubscriptionEvent

    with db.get_session() as session:
        # Get queue stats
        pending_jobs = session.query(JobQueue).filter(
            JobQueue.status == 'pending'
        ).count()

        running_jobs = session.query(JobQueue).filter(
            JobQueue.status == 'running'
        ).count()

        failed_today = session.query(JobQueue).filter(
            JobQueue.status == 'failed',
            JobQueue.updated_at >= datetime.utcnow() - timedelta(days=1)
        ).count()

        # Get last processed meeting
        last_meeting = session.query(Meeting).filter(
            Meeting.status == 'completed'
        ).order_by(Meeting.updated_at.desc()).first()

        last_meeting_time = None
        last_meeting_subject = None
        if last_meeting:
            last_meeting_time = last_meeting.updated_at.isoformat() + 'Z' if last_meeting.updated_at else None
            last_meeting_subject = last_meeting.subject

        # Get webhook subscription status
        active_subscription = session.query(SubscriptionEvent).filter(
            SubscriptionEvent.event_type.in_(['created', 'renewed', 'recovered'])
        ).order_by(SubscriptionEvent.timestamp.desc()).first()

        last_down_event = session.query(SubscriptionEvent).filter(
            SubscriptionEvent.event_type.in_(['down', 'failed', 'expired'])
        ).order_by(SubscriptionEvent.timestamp.desc()).first()

        webhook_active = False
        webhook_expires = None
        if active_subscription:
            # Check if there's a more recent down event
            if last_down_event and last_down_event.timestamp > active_subscription.timestamp:
                webhook_active = False
            else:
                webhook_active = True
                # Try to get expiration from subscription details
                if active_subscription.details and 'expirationDateTime' in active_subscription.details:
                    webhook_expires = active_subscription.details['expirationDateTime']

        return {
            "webhook": {
                "active": webhook_active,
                "expires_at": webhook_expires
            },
            "queue": {
                "pending": pending_jobs,
                "running": running_jobs,
                "failed_today": failed_today
            },
            "last_meeting": {
                "time": last_meeting_time,
                "subject": last_meeting_subject
            }
        }


@router.get("/api/dashboard/meeting-breakdown")
async def get_meeting_breakdown(days: int = Query(default=7)):
    """
    Get meeting breakdown by type and processing status.

    Returns:
        - scheduled vs ad-hoc vs p2p counts
        - transcript/summary rates
        - no transcript/skipped counts
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Meeting)
        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        total = query.count()

        # By call type
        group_calls = query.filter(Meeting.call_type == 'groupCall').count()
        peer_to_peer = query.filter(Meeting.call_type == 'peerToPeer').count()
        scheduled = query.filter(Meeting.discovery_source == 'calendar').count()
        adhoc = total - scheduled  # Meetings not from calendar are ad-hoc

        # Processing status
        with_transcript = query.filter(Meeting.has_transcript == True).count()
        with_summary = query.filter(Meeting.has_summary == True).count()
        distributed = query.filter(Meeting.has_distribution == True).count()
        no_transcript = query.filter(
            Meeting.status.in_(['no_transcript', 'transcription_disabled'])
        ).count()
        skipped = query.filter(Meeting.status == 'skipped').count()

        return {
            "total": total,
            "by_type": {
                "group_calls": group_calls,
                "peer_to_peer": peer_to_peer,
                "scheduled": scheduled,
                "adhoc": adhoc
            },
            "processing": {
                "with_transcript": with_transcript,
                "transcript_rate": round(with_transcript / total * 100, 1) if total > 0 else 0,
                "with_summary": with_summary,
                "summary_rate": round(with_summary / total * 100, 1) if total > 0 else 0,
                "distributed": distributed,
                "distribution_rate": round(distributed / total * 100, 1) if total > 0 else 0,
                "no_transcript": no_transcript,
                "skipped": skipped
            }
        }


@router.get("/api/dashboard/meeting-types")
async def get_meeting_types(days: int = Query(default=7)):
    """
    Get meeting type classification breakdown.

    Returns:
        - meeting type counts
        - meeting category counts (internal/external)
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        # Join summaries with meetings to get classification data
        query = session.query(
            Summary.meeting_type,
            func.count(Summary.id).label('count')
        ).join(Meeting).filter(
            Summary.meeting_type.isnot(None)
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        type_counts = query.group_by(Summary.meeting_type).all()

        # Get category counts
        cat_query = session.query(
            Summary.meeting_category,
            func.count(Summary.id).label('count')
        ).join(Meeting).filter(
            Summary.meeting_category.isnot(None)
        )

        if start_date:
            cat_query = cat_query.filter(Meeting.start_time >= start_date)

        category_counts = cat_query.group_by(Summary.meeting_category).all()

        return {
            "by_type": {r.meeting_type: r.count for r in type_counts if r.meeting_type},
            "by_category": {r.meeting_category: r.count for r in category_counts if r.meeting_category}
        }


@router.get("/api/dashboard/insights")
async def get_dashboard_insights(days: int = Query(default=7)):
    """
    Get aggregated insights from meeting summaries.

    Returns:
        - action item counts
        - decision counts
        - concern counts
        - financial discussion counts
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Summary).join(Meeting)

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        # Aggregate counts
        total_action_items = session.query(
            func.sum(Summary.action_item_count)
        ).join(Meeting)
        if start_date:
            total_action_items = total_action_items.filter(Meeting.start_time >= start_date)
        action_items = total_action_items.scalar() or 0

        total_decisions = session.query(
            func.sum(Summary.decision_count)
        ).join(Meeting)
        if start_date:
            total_decisions = total_decisions.filter(Meeting.start_time >= start_date)
        decisions = total_decisions.scalar() or 0

        # Flag-based counts
        concerns_count = query.filter(Summary.has_concerns == True).count()
        escalations = query.filter(Summary.has_escalation == True).count()
        financial = query.filter(Summary.has_financial_discussion == True).count()
        follow_ups = query.filter(Summary.follow_up_required == True).count()
        external = query.filter(Summary.has_external_participants == True).count()

        return {
            "action_items": action_items,
            "decisions": decisions,
            "concerns": concerns_count,
            "escalations": escalations,
            "financial_discussions": financial,
            "follow_ups_required": follow_ups,
            "external_meetings": external
        }


@router.get("/api/dashboard/top-participants")
async def get_top_participants(days: int = Query(default=7), limit: int = Query(default=10)):
    """
    Get most active meeting participants.

    Returns:
        - participant name
        - meeting count
        - last meeting date
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(
            MeetingParticipant.display_name,
            MeetingParticipant.email,
            func.count(distinct(MeetingParticipant.meeting_id)).label('meeting_count')
        ).join(Meeting).filter(
            MeetingParticipant.attended == True,
            MeetingParticipant.display_name.isnot(None)
        )

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        query = query.group_by(
            MeetingParticipant.display_name,
            MeetingParticipant.email
        ).order_by(func.count(distinct(MeetingParticipant.meeting_id)).desc()).limit(limit)

        results = query.all()

        return {
            "participants": [
                {
                    "name": r.display_name or r.email or "Unknown",
                    "email": r.email,
                    "meetings": r.meeting_count
                }
                for r in results
            ]
        }


@router.get("/api/dashboard/sentiment-breakdown")
async def get_sentiment_breakdown(days: int = Query(default=7)):
    """
    Get meeting sentiment analysis breakdown.

    Returns:
        - sentiment counts (positive, neutral, negative, mixed)
        - effectiveness counts
        - urgency level counts
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        # Sentiment breakdown
        sentiment_query = session.query(
            Summary.overall_sentiment,
            func.count(Summary.id).label('count')
        ).join(Meeting).filter(
            Summary.overall_sentiment.isnot(None)
        )

        if start_date:
            sentiment_query = sentiment_query.filter(Meeting.start_time >= start_date)

        sentiment_counts = sentiment_query.group_by(Summary.overall_sentiment).all()

        # Effectiveness breakdown
        effectiveness_query = session.query(
            Summary.meeting_effectiveness,
            func.count(Summary.id).label('count')
        ).join(Meeting).filter(
            Summary.meeting_effectiveness.isnot(None)
        )

        if start_date:
            effectiveness_query = effectiveness_query.filter(Meeting.start_time >= start_date)

        effectiveness_counts = effectiveness_query.group_by(Summary.meeting_effectiveness).all()

        # Urgency breakdown
        urgency_query = session.query(
            Summary.urgency_level,
            func.count(Summary.id).label('count')
        ).join(Meeting).filter(
            Summary.urgency_level.isnot(None)
        )

        if start_date:
            urgency_query = urgency_query.filter(Meeting.start_time >= start_date)

        urgency_counts = urgency_query.group_by(Summary.urgency_level).all()

        return {
            "sentiment": {r.overall_sentiment: r.count for r in sentiment_counts if r.overall_sentiment},
            "effectiveness": {r.meeting_effectiveness: r.count for r in effectiveness_counts if r.meeting_effectiveness},
            "urgency": {r.urgency_level: r.count for r in urgency_counts if r.urgency_level}
        }


@router.get("/api/dashboard/quality-metrics")
async def get_quality_metrics(days: int = Query(default=7)):
    """
    Get meeting quality metrics from Graph API data.

    Returns:
        - average quality score
        - quality issue count
        - modality breakdown
        - device type breakdown
    """
    start_date = get_date_range(days)

    with db.get_session() as session:
        query = session.query(Meeting)

        if start_date:
            query = query.filter(Meeting.start_time >= start_date)

        total = query.count()

        # Quality metrics
        quality_issues = query.filter(Meeting.had_quality_issues == True).count()

        avg_quality = session.query(
            func.avg(Meeting.network_quality_score)
        )
        if start_date:
            avg_quality = avg_quality.filter(Meeting.start_time >= start_date)
        avg_score = avg_quality.scalar()

        # Modality breakdown
        video_count = query.filter(Meeting.primary_modality == 'video').count()
        audio_count = query.filter(Meeting.primary_modality == 'audio').count()
        screen_count = query.filter(Meeting.primary_modality == 'screenSharing').count()

        # PSTN calls
        pstn_count = query.filter(Meeting.is_pstn_call == True).count()

        return {
            "total_meetings": total,
            "quality": {
                "avg_score": round(float(avg_score), 2) if avg_score else None,
                "issues_count": quality_issues,
                "issue_rate": round(quality_issues / total * 100, 1) if total > 0 else 0
            },
            "modality": {
                "video": video_count,
                "audio": audio_count,
                "screen_sharing": screen_count
            },
            "pstn_calls": pstn_count
        }
