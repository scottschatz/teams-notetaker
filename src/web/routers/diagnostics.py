"""
Diagnostics Router

System diagnostics and monitoring endpoints.
"""

import logging
import subprocess
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse

from ...core.database import DatabaseManager, JobQueue, Meeting
from ...core.config import get_config
from ..app import limiter
from sqlalchemy import func, desc


logger = logging.getLogger(__name__)

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
    services = {}
    service_names = [
        'teams-notetaker-web',
        'teams-notetaker-webhook',
        'teams-notetaker-worker',
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
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'completed_at': job.completed_at.isoformat() if job.completed_at else None
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


@router.post("/api/force-lookback")
@limiter.limit("5/minute")  # Rate limit: max 5 backfills per minute
async def force_lookback(
    request: Request,
    hours: int = Query(..., ge=1, le=720),  # Max 30 days
    db: DatabaseManager = Depends(get_db)
):
    """
    Force a lookback/backfill for the specified number of hours.

    Rate limited to 5 requests per minute to prevent API abuse.

    Args:
        request: FastAPI request object (for rate limiting)
        hours: Number of hours to look back

    Returns:
        Success message with job count
    """
    try:
        from ...webhooks.call_records_handler import CallRecordsWebhookHandler
        from ...graph.client import GraphAPIClient
        from datetime import datetime, timedelta

        config = get_config()
        graph_client = GraphAPIClient(config.graph_api, use_beta=True)
        handler = CallRecordsWebhookHandler(db, graph_client)

        # Calculate lookback time
        lookback_start = datetime.now() - timedelta(hours=hours)

        logger.info(f"Force lookback triggered for last {hours} hours (from {lookback_start})")

        # Trigger backfill (FIXED: correct method name + await)
        stats = await handler.backfill_recent_meetings(lookback_hours=hours)

        return {
            "success": True,
            "message": f"Lookback complete for last {hours} hours",
            "lookback_start": lookback_start.isoformat(),
            "statistics": stats
        }

    except Exception as e:
        logger.error(f"Force lookback failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

    # Map service to log file
    log_files = {
        "worker": "logs/worker.log",
        "webhook": None,  # Uses journalctl
        "web": None  # Uses journalctl
    }

    log_entries = []

    if service == "worker" and os.path.exists("logs/worker.log"):
        # Read from file
        try:
            with open("logs/worker.log", "r") as f:
                all_lines = f.readlines()
                log_entries = [line.strip() for line in all_lines[-lines:]]
        except Exception as e:
            log_entries = [f"Error reading log: {e}"]
    else:
        # Use journalctl for systemd services
        try:
            service_name = f"teams-notetaker-{service}"
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
