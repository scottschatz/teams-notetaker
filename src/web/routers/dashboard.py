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


@router.get("/meetings/{meeting_id}/transcript", response_class=HTMLResponse)
async def meeting_transcript_page(
    request: Request,
    meeting_id: int
):
    """
    View transcript for a meeting.

    Args:
        meeting_id: Meeting ID

    Returns:
        HTML transcript page
    """
    from ...core.database import DatabaseManager, Meeting, Transcript, MeetingParticipant
    from ...core.config import get_config

    config = get_config()
    db = DatabaseManager(config.database.connection_string)

    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()
        transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()

        if not meeting:
            return HTMLResponse(content="<h1>Meeting not found</h1>", status_code=404)

        if not transcript:
            return HTMLResponse(content="<h1>No transcript available for this meeting</h1>", status_code=404)

        # Format transcript as readable HTML with speaker names and timestamps
        import re
        vtt_content = transcript.vtt_content or ""

        # Parse VTT format: timestamp line followed by <v Speaker>text</v>
        entries = []
        current_timestamp = None

        for line in vtt_content.split('\n'):
            line = line.strip()
            if not line or line.startswith('WEBVTT') or line.startswith('NOTE'):
                continue

            # Check for timestamp line (00:00:00.000 --> 00:00:00.000)
            if '-->' in line:
                # Extract start time only (e.g., "00:00:25")
                match = re.match(r'(\d+:\d+:\d+)', line)
                if match:
                    current_timestamp = match.group(1)
                continue

            # Parse speaker and text from <v Speaker Name>text</v>
            speaker_match = re.match(r'<v ([^>]+)>(.+?)</v>', line)
            if speaker_match:
                speaker = speaker_match.group(1)
                text = speaker_match.group(2)
                entries.append({
                    'timestamp': current_timestamp,
                    'speaker': speaker,
                    'text': text
                })
            elif line and not line.startswith('<'):
                # Plain text without speaker tag
                entries.append({
                    'timestamp': current_timestamp,
                    'speaker': None,
                    'text': line
                })

        # Build HTML with speaker grouping
        transcript_lines = []
        last_speaker = None
        for entry in entries:
            speaker = entry.get('speaker')
            text = entry.get('text', '')
            timestamp = entry.get('timestamp', '')

            if speaker and speaker != last_speaker:
                # New speaker - add speaker header
                transcript_lines.append(f'<p class="speaker"><strong>{speaker}</strong> <span class="timestamp">[{timestamp}]</span></p>')
                last_speaker = speaker

            transcript_lines.append(f'<p class="text">{text}</p>')

        transcript_html = '\n'.join(transcript_lines) if transcript_lines else "<p>Empty transcript</p>"

        # Get participant list
        participants = session.query(MeetingParticipant).filter_by(meeting_id=meeting_id).all()
        participant_list = ', '.join([p.display_name or p.email or 'Unknown' for p in participants]) if participants else 'N/A'

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Transcript - {meeting.subject}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
                h1 {{ color: #333; border-bottom: 2px solid #6366f1; padding-bottom: 10px; }}
                .meta {{ color: #666; margin-bottom: 20px; }}
                .transcript {{ background: #f9fafb; padding: 20px; border-radius: 8px; }}
                .speaker {{ margin-top: 16px; margin-bottom: 4px; color: #4f46e5; }}
                .timestamp {{ color: #9ca3af; font-weight: normal; font-size: 0.85em; }}
                .text {{ margin: 4px 0 4px 16px; }}
            </style>
        </head>
        <body>
            <h1>Transcript: {meeting.subject}</h1>
            <div class="meta">
                <strong>Organizer:</strong> {meeting.organizer_name}<br>
                <strong>Date:</strong> {meeting.start_time}<br>
                <strong>Word Count:</strong> {transcript.word_count or 'N/A'}<br>
                <strong>Speakers:</strong> {transcript.speaker_count or 'N/A'}<br>
                <strong>Participants:</strong> {participant_list}
            </div>
            <div class="transcript">
                {transcript_html}
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)


@router.get("/meetings/{meeting_id}/summary", response_class=HTMLResponse)
async def meeting_summary_page(
    request: Request,
    meeting_id: int
):
    """
    View summary for a meeting.

    Args:
        meeting_id: Meeting ID

    Returns:
        HTML summary page
    """
    from ...core.database import DatabaseManager, Meeting, Summary
    from ...core.config import get_config

    config = get_config()
    db = DatabaseManager(config.database.connection_string)

    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()
        summary = session.query(Summary).filter_by(meeting_id=meeting_id).order_by(Summary.version.desc()).first()

        if not meeting:
            return HTMLResponse(content="<h1>Meeting not found</h1>", status_code=404)

        if not summary:
            return HTMLResponse(content="<h1>No summary available for this meeting</h1>", status_code=404)

        # Use the pre-generated HTML if available, otherwise convert markdown
        summary_html = summary.summary_html or summary.summary_text or "<p>Empty summary</p>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Summary - {meeting.subject}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
                h1 {{ color: #333; border-bottom: 2px solid #14b8a6; padding-bottom: 10px; }}
                h2 {{ color: #374151; margin-top: 24px; }}
                .meta {{ color: #666; margin-bottom: 20px; }}
                .summary {{ background: #f9fafb; padding: 20px; border-radius: 8px; }}
                .summary h2 {{ margin-top: 16px; }}
                .summary ul, .summary ol {{ margin: 8px 0; padding-left: 24px; }}
                .summary p {{ margin: 8px 0; }}
            </style>
        </head>
        <body>
            <h1>Summary: {meeting.subject}</h1>
            <div class="meta">
                <strong>Organizer:</strong> {meeting.organizer_name}<br>
                <strong>Date:</strong> {meeting.start_time}<br>
                <strong>Model:</strong> {summary.model or 'N/A'}<br>
                <strong>Tokens:</strong> {summary.total_tokens or 'N/A'}
            </div>
            <div class="summary">
                {summary_html}
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)


@router.get("/meetings/{meeting_id}/transcript/download")
async def download_transcript(
    request: Request,
    meeting_id: int
):
    """
    Download transcript as VTT file.

    Args:
        meeting_id: Meeting ID

    Returns:
        VTT file download
    """
    from fastapi.responses import Response
    from ...core.database import DatabaseManager, Meeting, Transcript
    from ...core.config import get_config

    config = get_config()
    db = DatabaseManager(config.database.connection_string)

    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()
        transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()

        if not meeting:
            return HTMLResponse(content="Meeting not found", status_code=404)

        if not transcript or not transcript.vtt_content:
            return HTMLResponse(content="No transcript available", status_code=404)

        # Create filename from meeting subject
        subject_clean = "".join(c for c in (meeting.subject or "meeting") if c.isalnum() or c in " -_").strip()
        subject_clean = subject_clean[:50] if subject_clean else "meeting"
        filename = f"{subject_clean}_transcript.vtt"

        return Response(
            content=transcript.vtt_content,
            media_type="text/vtt",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )


@router.get("/meetings/{meeting_id}/summary/download")
async def download_summary(
    request: Request,
    meeting_id: int
):
    """
    Download summary as markdown file.

    Args:
        meeting_id: Meeting ID

    Returns:
        Markdown file download
    """
    from fastapi.responses import Response
    from ...core.database import DatabaseManager, Meeting, Summary
    from ...core.config import get_config

    config = get_config()
    db = DatabaseManager(config.database.connection_string)

    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()
        summary = session.query(Summary).filter_by(meeting_id=meeting_id).order_by(Summary.version.desc()).first()

        if not meeting:
            return HTMLResponse(content="Meeting not found", status_code=404)

        if not summary or not summary.summary_text:
            return HTMLResponse(content="No summary available", status_code=404)

        # Create filename from meeting subject
        subject_clean = "".join(c for c in (meeting.subject or "meeting") if c.isalnum() or c in " -_").strip()
        subject_clean = subject_clean[:50] if subject_clean else "meeting"
        filename = f"{subject_clean}_summary.md"

        # Add meeting header to markdown
        import pytz
        eastern = pytz.timezone('America/New_York')

        time_str = ""
        if meeting.start_time:
            start_utc = meeting.start_time.replace(tzinfo=pytz.UTC) if meeting.start_time.tzinfo is None else meeting.start_time
            time_str = start_utc.astimezone(eastern).strftime("%a, %b %d, %Y at %I:%M %p %Z")

        content = f"""# {meeting.subject or 'Meeting Summary'}

**Organizer:** {meeting.organizer_name or 'Unknown'}
**Date:** {time_str}
**Duration:** {meeting.duration_minutes or 'N/A'} minutes

---

{summary.summary_text}
"""

        return Response(
            content=content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
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
