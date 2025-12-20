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
            model = row.model or ''

            if 'haiku' in model.lower():
                # Haiku: $1/$5 per million tokens (input/output)
                total_cost += (input_tokens * 1 + output_tokens * 5) / 1_000_000
            elif 'sonnet' in model.lower():
                # Sonnet: $3/$15 per million tokens
                total_cost += (input_tokens * 3 + output_tokens * 15) / 1_000_000

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
                model = row.model or ''

                if 'haiku' in model.lower():
                    prev_cost += (input_tokens * 1 + output_tokens * 5) / 1_000_000
                elif 'sonnet' in model.lower():
                    prev_cost += (input_tokens * 3 + output_tokens * 15) / 1_000_000

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
                dates[date_str] = {"haiku": 0, "sonnet": 0, "other": 0}

            input_tokens = r.input_tokens or 0
            output_tokens = r.output_tokens or 0
            model = (r.model or '').lower()

            if 'haiku' in model:
                cost = (input_tokens * 1 + output_tokens * 5) / 1_000_000
                dates[date_str]["haiku"] += cost
            elif 'sonnet' in model:
                cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
                dates[date_str]["sonnet"] += cost
            else:
                dates[date_str]["other"] += (input_tokens + output_tokens) / 1_000_000

        sorted_dates = sorted(dates.keys())

        return {
            "labels": sorted_dates,
            "datasets": {
                "haiku": [round(dates[d]["haiku"], 4) for d in sorted_dates],
                "sonnet": [round(dates[d]["sonnet"], 4) for d in sorted_dates],
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


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate AI cost based on model and token usage."""
    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0
    model = (model or '').lower()

    if 'haiku' in model:
        return round((prompt_tokens * 1 + completion_tokens * 5) / 1_000_000, 4)
    elif 'sonnet' in model:
        return round((prompt_tokens * 3 + completion_tokens * 15) / 1_000_000, 4)
    else:
        return round((prompt_tokens + completion_tokens) / 1_000_000, 4)
