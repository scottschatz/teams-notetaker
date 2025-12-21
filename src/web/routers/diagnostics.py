"""
Diagnostics Router

System diagnostics and monitoring endpoints.
"""

import asyncio
import logging
import subprocess
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
from typing import Dict, Any, Optional

from ...core.database import DatabaseManager, JobQueue, Meeting, SubscriptionEvent, Summary
from ...core.config import get_config
from ...graph.client import GraphAPIClient
from ..app import limiter
from sqlalchemy import func, desc, and_


logger = logging.getLogger(__name__)

# In-memory storage for background lookback tasks
_lookback_tasks: Dict[str, Dict[str, Any]] = {}

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def get_db() -> DatabaseManager:
    """Get database manager instance."""
    config = get_config()
    return DatabaseManager(config.database.connection_string)


@router.get("/api/status")
async def get_system_status(db: DatabaseManager = Depends(get_db)):
    """
    Get comprehensive system status.

    Returns:
        System diagnostics including services, queue, and activity
    """
    # Check systemd services
    # Note: webhook and worker were consolidated into poller service
    services = {}
    service_names = [
        'teams-notetaker-web',
        'teams-notetaker-poller'
    ]

    for service in service_names:
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', f'{service}.service'],
                capture_output=True,
                text=True,
                timeout=2
            )
            services[service] = {
                'status': result.stdout.strip(),
                'running': result.stdout.strip() == 'active'
            }

            # Get service uptime
            if services[service]['running']:
                uptime_result = subprocess.run(
                    ['systemctl', '--user', 'show', f'{service}.service',
                     '--property=ActiveEnterTimestamp'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                timestamp_line = uptime_result.stdout.strip()
                if '=' in timestamp_line:
                    services[service]['started_at'] = timestamp_line.split('=')[1]
        except Exception as e:
            services[service] = {
                'status': 'error',
                'running': False,
                'error': str(e)
            }

    # Get queue statistics
    with db.get_session() as session:
        # Job queue stats
        queue_stats = {
            'pending': session.query(JobQueue).filter_by(status='pending').count(),
            'running': session.query(JobQueue).filter_by(status='running').count(),
            'completed': session.query(JobQueue).filter_by(status='completed').count(),
            'failed': session.query(JobQueue).filter_by(status='failed').count(),
            'retrying': session.query(JobQueue).filter_by(status='retrying').count()
        }

        # Recent job activity (last hour)
        one_hour_ago = datetime.now() - timedelta(hours=1)
        recent_jobs = session.query(
            JobQueue.job_type,
            JobQueue.status,
            func.count(JobQueue.id).label('count')
        ).filter(
            JobQueue.created_at > one_hour_ago
        ).group_by(JobQueue.job_type, JobQueue.status).all()

        job_activity = {}
        for job_type, status, count in recent_jobs:
            if job_type not in job_activity:
                job_activity[job_type] = {}
            job_activity[job_type][status] = count

        # Meeting stats
        meeting_stats = {
            'total': session.query(Meeting).count(),
            'completed': session.query(Meeting).filter_by(status='completed').count(),
            'processing': session.query(Meeting).filter_by(status='processing').count(),
            'queued': session.query(Meeting).filter_by(status='queued').count(),
            'failed': session.query(Meeting).filter_by(status='failed').count(),
            'skipped': session.query(Meeting).filter_by(status='skipped').count()
        }

        # Recent activity (use discovered_at if available, otherwise skip)
        try:
            recent_meetings = session.query(Meeting).filter(
                Meeting.discovered_at > one_hour_ago
            ).count()
        except AttributeError:
            # If discovered_at doesn't exist, count all meetings discovered today
            from datetime import date
            today_start = datetime.combine(date.today(), datetime.min.time())
            recent_meetings = session.query(Meeting).filter(
                Meeting.start_time > today_start
            ).count() if Meeting.start_time else 0

        # Latest 10 jobs
        latest_jobs = session.query(JobQueue).order_by(
            desc(JobQueue.created_at)
        ).limit(10).all()

        latest_jobs_data = []
        for job in latest_jobs:
            meeting_id = job.input_data.get('meeting_id') if job.input_data else None
            latest_jobs_data.append({
                'id': job.id,
                'type': job.job_type,
                'status': job.status,
                'meeting_id': meeting_id,
                # Add 'Z' suffix to indicate UTC (database stores UTC-naive)
                'created_at': job.created_at.isoformat() + 'Z' if job.created_at else None,
                'completed_at': job.completed_at.isoformat() + 'Z' if job.completed_at else None
            })

    return {
        'timestamp': datetime.now().isoformat(),
        'services': services,
        'queue': {
            'stats': queue_stats,
            'total_active': queue_stats['pending'] + queue_stats['running'],
            'activity_last_hour': job_activity
        },
        'meetings': {
            'stats': meeting_stats,
            'discovered_last_hour': recent_meetings
        },
        'recent_jobs': latest_jobs_data
    }


async def _run_lookback_task(task_id: str, hours: int, db: DatabaseManager):
    """Background task to run lookback without blocking the web server."""
    try:
        from ...webhooks.call_records_handler import CallRecordsWebhookHandler

        config = get_config()
        graph_client = GraphAPIClient(config.graph_api, use_beta=True)
        handler = CallRecordsWebhookHandler(db, graph_client)

        lookback_start = datetime.utcnow() - timedelta(hours=hours)

        logger.info(f"Background lookback {task_id} started for last {hours} hours")

        # Run the backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=hours)

        # Update task with results
        _lookback_tasks[task_id].update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat() + 'Z',
            "success": True,
            "lookback_start": lookback_start.isoformat() + 'Z',
            "statistics": stats
        })

        logger.info(f"Background lookback {task_id} completed: {stats}")

    except Exception as e:
        logger.error(f"Background lookback {task_id} failed: {e}", exc_info=True)
        _lookback_tasks[task_id].update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat() + 'Z',
            "success": False,
            "error": str(e)
        })


@router.post("/api/force-lookback")
@limiter.limit("5/minute")  # Rate limit: max 5 backfills per minute
async def force_lookback(
    request: Request,
    hours: int = Query(..., ge=1, le=720),  # Max 30 days
    db: DatabaseManager = Depends(get_db)
):
    """
    Force a lookback/backfill for the specified number of hours.

    Runs in background to avoid blocking the web server.
    Returns immediately with a task_id to poll for results.

    Rate limited to 5 requests per minute to prevent API abuse.
    """
    # Check if there's already a running lookback
    for task_id, task in _lookback_tasks.items():
        if task.get("status") == "running":
            return {
                "success": False,
                "message": "A lookback is already running",
                "task_id": task_id,
                "hours": task.get("hours"),
                "started_at": task.get("started_at")
            }

    # Create a new task
    task_id = str(uuid.uuid4())[:8]
    _lookback_tasks[task_id] = {
        "status": "running",
        "hours": hours,
        "started_at": datetime.utcnow().isoformat() + 'Z'
    }

    logger.info(f"Force lookback triggered for last {hours} hours (task_id={task_id})")

    # Start background task
    asyncio.create_task(_run_lookback_task(task_id, hours, db))

    return {
        "success": True,
        "message": f"Lookback started for last {hours} hours",
        "task_id": task_id,
        "status": "running"
    }


@router.get("/api/lookback-status/{task_id}")
async def get_lookback_status(task_id: str):
    """
    Get the status of a background lookback task.
    """
    task = _lookback_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "task_id": task_id,
        **task
    }


@router.get("/api/lookback-status")
async def get_current_lookback_status():
    """
    Get the status of any currently running lookback, or the most recent completed one.
    """
    # Find running task first
    for task_id, task in _lookback_tasks.items():
        if task.get("status") == "running":
            return {
                "task_id": task_id,
                **task
            }

    # Return most recent completed task
    if _lookback_tasks:
        # Get most recent by started_at
        recent = max(_lookback_tasks.items(), key=lambda x: x[1].get("started_at", ""))
        return {
            "task_id": recent[0],
            **recent[1]
        }

    return {"status": "none", "message": "No lookback tasks found"}


@router.get("/api/logs")
async def get_recent_logs(
    lines: int = Query(default=50, ge=10, le=500),
    service: str = Query(default="worker")
):
    """
    Get recent log entries from service logs.

    Args:
        lines: Number of lines to return (default 50)
        service: Service to get logs from (worker, webhook, web)

    Returns:
        List of recent log entries
    """
    import os

    # Map service to systemd service name
    service_map = {
        "poller": "teams-notetaker-poller",  # Consolidated service (webhooks + worker)
        "web": "teams-notetaker-web",
        # Legacy mappings (redirect to poller)
        "worker": "teams-notetaker-poller",
        "webhook": "teams-notetaker-poller"
    }

    log_entries = []

    # Use journalctl for all systemd services
    try:
        service_name = service_map.get(service, f"teams-notetaker-{service}")
        result = subprocess.run(
            ["journalctl", "--user", "-u", service_name, "-n", str(lines), "--no-pager", "-o", "short"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            log_entries = [line for line in result.stdout.strip().split("\n") if line]
        else:
            log_entries = [f"Error: {result.stderr}"]
    except Exception as e:
        log_entries = [f"Error getting logs: {e}"]

    return {
        "service": service,
        "lines": len(log_entries),
        "entries": log_entries
    }


@router.get("/", response_class=HTMLResponse)
async def diagnostics_page(request: Request):
    """
    Diagnostics dashboard page.

    Returns:
        HTML diagnostics page
    """
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "user": {"email": "local", "role": "admin"},
            "page": "diagnostics"
        }
    )


@router.get("/backfill-history", response_class=HTMLResponse)
async def backfill_history_page(
    request: Request,
    db: DatabaseManager = Depends(get_db)
):
    """Display backfill run history."""
    from ...core.database import BackfillRun

    templates = request.app.state.templates

    with db.get_session() as session:
        # Get last 50 backfill runs
        runs = session.query(BackfillRun).order_by(
            BackfillRun.started_at.desc()
        ).limit(50).all()

        return templates.TemplateResponse(
            "diagnostics/backfill_history.html",
            {
                "request": request,
                "runs": runs,
                "page_title": "Backfill History",
                "user": {"email": "local", "role": "admin"}
            }
        )


@router.get("/api/webhook-status")
async def get_webhook_status():
    """
    Get current webhook subscription status.

    Returns:
        Subscription info including active status, expiration, and health
    """
    try:
        config = get_config()

        # Check if Azure Relay is configured
        if not config.azure_relay.is_configured():
            return {
                "configured": False,
                "message": "Azure Relay not configured",
                "subscriptions": []
            }

        graph_client = GraphAPIClient(config.graph_api)
        webhook_url = config.azure_relay.webhook_url

        # Get all subscriptions
        response = graph_client.get("/subscriptions")
        all_subs = response.get("value", [])

        # Filter to callRecords subscriptions for our webhook
        callrecords_subs = [
            sub for sub in all_subs
            if sub.get("resource") == "/communications/callRecords"
            and sub.get("notificationUrl") == webhook_url
        ]

        # Parse and enrich subscription data
        now = datetime.utcnow()
        subscriptions = []
        active_count = 0

        for sub in callrecords_subs:
            expiry_str = sub.get("expirationDateTime", "")
            try:
                expiry = datetime.fromisoformat(expiry_str.replace("Z", ""))
                hours_remaining = (expiry - now).total_seconds() / 3600
                is_active = hours_remaining > 0
                is_expiring_soon = 0 < hours_remaining < 12

                if is_active:
                    active_count += 1

                subscriptions.append({
                    "id": sub.get("id"),
                    "resource": sub.get("resource"),
                    "expiration": expiry_str,
                    "hours_remaining": round(hours_remaining, 1),
                    "is_active": is_active,
                    "is_expiring_soon": is_expiring_soon,
                    "created": sub.get("createdDateTime")
                })
            except Exception as e:
                logger.warning(f"Error parsing subscription expiry: {e}")
                subscriptions.append({
                    "id": sub.get("id"),
                    "resource": sub.get("resource"),
                    "expiration": expiry_str,
                    "error": str(e)
                })

        # If subscription is down, find when it went down
        down_since = None
        down_duration = None
        if active_count == 0:
            db = get_db()
            with db.get_session() as session:
                # Find most recent 'down' or 'failed' event without a corresponding 'up'
                from sqlalchemy import desc
                last_down = session.query(SubscriptionEvent).filter(
                    SubscriptionEvent.event_type.in_(['down', 'failed'])
                ).order_by(desc(SubscriptionEvent.timestamp)).first()

                if last_down:
                    # Check if there's a more recent 'up' or 'created' event
                    recovery = session.query(SubscriptionEvent).filter(
                        SubscriptionEvent.event_type.in_(['up', 'created']),
                        SubscriptionEvent.timestamp > last_down.timestamp
                    ).first()

                    if not recovery:
                        down_since = last_down.timestamp.isoformat() + 'Z'
                        down_duration = int((now - last_down.timestamp).total_seconds())

        return {
            "configured": True,
            "webhook_url": webhook_url,
            "active": active_count > 0,
            "active_count": active_count,
            "total_count": len(subscriptions),
            "subscriptions": subscriptions,
            "checked_at": now.isoformat() + 'Z',  # UTC timestamp
            "down_since": down_since,
            "down_duration_seconds": down_duration
        }

    except Exception as e:
        logger.error(f"Error getting webhook status: {e}")
        return {
            "configured": True,
            "active": False,
            "error": str(e),
            "subscriptions": []
        }


@router.post("/api/webhook-resubscribe")
@limiter.limit("3/minute")  # Rate limit: max 3 attempts per minute
async def force_webhook_resubscribe(request: Request):
    """
    Force recreate webhook subscription.

    Deletes existing subscriptions and creates a fresh one.
    Rate limited to 3 requests per minute.
    """
    try:
        from ...webhooks.subscription_manager import SubscriptionManager

        config = get_config()

        if not config.azure_relay.is_configured():
            raise HTTPException(status_code=400, detail="Azure Relay not configured")

        graph_client = GraphAPIClient(config.graph_api)
        manager = SubscriptionManager(config, graph_client)

        logger.info("Force resubscribe triggered from diagnostics page")

        # Delete all existing and create fresh (with 'manual' source)
        success = await manager.recreate_subscription('manual')

        if success:
            # Get the new subscription info
            subscriptions = manager.get_callrecords_subscriptions()
            sub_info = subscriptions[0] if subscriptions else None

            return {
                "success": True,
                "message": "Webhook subscription recreated successfully",
                "subscription": {
                    "id": sub_info.get("id") if sub_info else None,
                    "expiration": sub_info.get("expirationDateTime") if sub_info else None
                }
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to create webhook subscription. Check Azure Relay connection."
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Force resubscribe failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/backfill-history")
async def get_backfill_history(
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(get_db)
):
    """Get backfill run history as JSON."""
    from ...core.database import BackfillRun

    with db.get_session() as session:
        runs = session.query(BackfillRun).order_by(
            BackfillRun.started_at.desc()
        ).limit(limit).all()

        return {
            "runs": [
                {
                    "id": run.id,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "status": run.status,
                    "source": run.source,
                    "lookback_hours": run.lookback_hours,
                    "statistics": {
                        "call_records_found": run.call_records_found,
                        "meetings_created": run.meetings_created,
                        "transcripts_found": run.transcripts_found,
                        "transcripts_pending": run.transcripts_pending,
                        "skipped_no_optin": run.skipped_no_optin,
                        "jobs_created": run.jobs_created,
                        "errors": run.errors
                    },
                    "error_message": run.error_message
                }
                for run in runs
            ]
        }


@router.get("/api/subscription-stats")
async def get_subscription_stats(
    days: int = Query(default=7, ge=1, le=365),
    db: DatabaseManager = Depends(get_db)
):
    """
    Get subscription uptime statistics for the specified time period.

    Args:
        days: Number of days to analyze (1-365, default 7)

    Returns:
        Statistics including uptime %, disconnect count, average downtime
    """
    with db.get_session() as session:
        # Calculate date range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        total_period_seconds = days * 24 * 3600

        # Get all events in the time period
        events = session.query(SubscriptionEvent).filter(
            SubscriptionEvent.timestamp >= start_time
        ).order_by(SubscriptionEvent.timestamp.desc()).all()

        # Count events by type
        down_events = [e for e in events if e.event_type == 'down']
        up_events = [e for e in events if e.event_type == 'up']
        created_events = [e for e in events if e.event_type == 'created']
        renewed_events = [e for e in events if e.event_type == 'renewed']
        failed_events = [e for e in events if e.event_type == 'failed']

        # Calculate total downtime from 'up' events that have downtime_seconds
        downtime_values = [e.downtime_seconds for e in up_events if e.downtime_seconds is not None]
        total_downtime_seconds = sum(downtime_values)

        # Calculate average downtime per incident
        avg_downtime_seconds = total_downtime_seconds / len(downtime_values) if downtime_values else 0

        # Calculate uptime percentage
        uptime_seconds = total_period_seconds - total_downtime_seconds
        uptime_percentage = (uptime_seconds / total_period_seconds) * 100 if total_period_seconds > 0 else 100

        # Format recent events for display
        recent_events = []
        for event in events[:20]:  # Last 20 events
            event_data = {
                "id": event.id,
                "event_type": event.event_type,
                "timestamp": event.timestamp.isoformat() + 'Z' if event.timestamp else None,
                "source": event.source,
                "subscription_id": event.subscription_id[:20] + "..." if event.subscription_id and len(event.subscription_id) > 20 else event.subscription_id
            }

            if event.event_type == 'down' or event.event_type == 'failed':
                event_data["error_message"] = event.error_message

            if event.event_type == 'up' and event.downtime_seconds is not None:
                event_data["downtime_seconds"] = event.downtime_seconds
                event_data["downtime_formatted"] = _format_duration(event.downtime_seconds)

            recent_events.append(event_data)

        return {
            "period": {
                "days": days,
                "start": start_time.isoformat() + 'Z',
                "end": end_time.isoformat() + 'Z'
            },
            "summary": {
                "uptime_percentage": round(uptime_percentage, 2),
                "total_downtime_seconds": total_downtime_seconds,
                "total_downtime_formatted": _format_duration(total_downtime_seconds),
                "avg_downtime_seconds": round(avg_downtime_seconds, 0),
                "avg_downtime_formatted": _format_duration(int(avg_downtime_seconds))
            },
            "counts": {
                "down_events": len(down_events),
                "up_events": len(up_events),
                "created": len(created_events),
                "renewed": len(renewed_events),
                "failed": len(failed_events),
                "total_events": len(events)
            },
            "recent_events": recent_events
        }


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


@router.get("/api/ai-model-stats")
async def get_ai_model_stats(
    days: int = Query(default=7, ge=1, le=365),
    db: DatabaseManager = Depends(get_db)
):
    """
    Get AI model usage statistics including Gemini vs Haiku fallback rates.

    Args:
        days: Number of days to analyze (1-365, default 7)

    Returns:
        Statistics including approach counts, fallback rate, costs
    """
    with db.get_session() as session:
        # Calculate date range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)

        # Get summaries in time period
        summaries = session.query(Summary).filter(
            Summary.generated_at >= start_time
        ).all()

        # Count by approach
        approach_counts = {}
        model_counts = {}
        total_cost = 0.0
        total_tokens = 0

        for s in summaries:
            approach = s.approach or "legacy"  # Pre-Gemini summaries
            model = s.model or "unknown"

            approach_counts[approach] = approach_counts.get(approach, 0) + 1
            model_counts[model] = model_counts.get(model, 0) + 1

            if s.total_tokens:
                total_tokens += s.total_tokens

            # Estimate cost based on model
            if s.prompt_tokens and s.completion_tokens:
                if "gemini" in model.lower():
                    cost = (s.prompt_tokens * 0.50 + s.completion_tokens * 3.00) / 1_000_000
                elif "haiku" in model.lower():
                    cost = (s.prompt_tokens * 1.00 + s.completion_tokens * 5.00) / 1_000_000
                else:
                    cost = (s.prompt_tokens * 3.00 + s.completion_tokens * 15.00) / 1_000_000
                total_cost += cost

        # Calculate rates
        total_summaries = len(summaries)
        gemini_count = approach_counts.get("gemini_single_call", 0)
        haiku_fallback_count = approach_counts.get("haiku_fallback", 0)
        legacy_count = approach_counts.get("legacy", 0) + approach_counts.get("single_call", 0) + approach_counts.get("multi_stage", 0)

        # Fallback rate = haiku_fallback / (gemini + haiku_fallback)
        gemini_attempts = gemini_count + haiku_fallback_count
        fallback_rate = (haiku_fallback_count / gemini_attempts * 100) if gemini_attempts > 0 else 0

        # Recent fallbacks (last 10)
        recent_fallbacks = session.query(Summary).filter(
            Summary.generated_at >= start_time,
            Summary.approach == "haiku_fallback"
        ).order_by(desc(Summary.generated_at)).limit(10).all()

        recent_fallback_data = []
        for s in recent_fallbacks:
            meeting = s.meeting
            recent_fallback_data.append({
                "summary_id": s.id,
                "meeting_id": s.meeting_id,
                "subject": meeting.subject if meeting else "Unknown",
                "generated_at": s.generated_at.isoformat() + 'Z' if s.generated_at else None,
                "model": s.model
            })

        return {
            "period": {
                "days": days,
                "start": start_time.isoformat() + 'Z',
                "end": end_time.isoformat() + 'Z'
            },
            "summary": {
                "total_summaries": total_summaries,
                "gemini_count": gemini_count,
                "haiku_fallback_count": haiku_fallback_count,
                "legacy_count": legacy_count,
                "fallback_rate_percent": round(fallback_rate, 2),
                "total_tokens": total_tokens,
                "estimated_cost": round(total_cost, 4)
            },
            "approach_counts": approach_counts,
            "model_counts": model_counts,
            "recent_fallbacks": recent_fallback_data
        }
