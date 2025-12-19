"""
Meetings API Router

REST API endpoints for meetings data.
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from sqlalchemy import func
from ...core.database import DatabaseManager, Meeting, Summary, Transcript, MeetingParticipant, JobQueue
from ...core.config import get_config
from ...jobs.queue import JobQueueManager


logger = logging.getLogger(__name__)

router = APIRouter()

# Database dependency (no auth needed for local use)
def get_db() -> DatabaseManager:
    """Get database manager instance."""
    config = get_config()
    return DatabaseManager(config.database.connection_string)


class MeetingResponse(BaseModel):
    """Meeting API response."""
    id: int
    meeting_id: str
    subject: Optional[str] = None
    organizer_name: Optional[str] = None
    organizer_email: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    participant_count: Optional[int] = None
    status: Optional[str] = None
    has_transcript: bool = False
    has_summary: bool = False
    has_distribution: bool = False
    word_count: Optional[int] = None
    speaker_count: Optional[int] = None
    # Discovery fields
    discovery_source: Optional[str] = None
    discovered_at: Optional[datetime] = None
    # Summary/AI fields
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    generation_time_ms: Optional[int] = None

    class Config:
        from_attributes = True


class MeetingDetailResponse(MeetingResponse):
    """Detailed meeting response with summary and transcript."""
    summary_text: Optional[str] = None
    transcript_preview: Optional[str] = None
    participants: List[dict] = []


@router.get("/", response_model=List[MeetingResponse])
async def list_meetings(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    status: Optional[str] = None,
    participant: Optional[str] = None,
    db: DatabaseManager = Depends(get_db)
):
    """
    List meetings with pagination.

    Args:
        skip: Number of records to skip
        limit: Number of records to return
        status: Filter by status
        participant: Filter by participant email
        db: Database manager

    Returns:
        List of meetings
    """
    with db.get_session() as session:
        # Subquery to get the latest summary version for each meeting
        latest_summary_version = session.query(
            Summary.meeting_id,
            func.max(Summary.version).label('max_version')
        ).group_by(Summary.meeting_id).subquery()

        # Join with transcript and LATEST summary to get word_count, speaker_count, and AI stats
        query = session.query(
            Meeting,
            Transcript.word_count,
            Transcript.speaker_count,
            Summary.model,
            Summary.prompt_tokens,
            Summary.completion_tokens,
            Summary.total_tokens,
            Summary.generation_time_ms
        ).outerjoin(
            Transcript, Meeting.id == Transcript.meeting_id
        ).outerjoin(
            latest_summary_version,
            Meeting.id == latest_summary_version.c.meeting_id
        ).outerjoin(
            Summary,
            (Meeting.id == Summary.meeting_id) &
            (Summary.version == latest_summary_version.c.max_version)
        )

        if status:
            query = query.filter(Meeting.status == status)

        if participant:
            # Filter by participant email
            query = query.join(MeetingParticipant, Meeting.id == MeetingParticipant.meeting_id)
            query = query.filter(MeetingParticipant.email == participant)

        query = query.order_by(Meeting.start_time.desc())

        results = query.offset(skip).limit(limit).all()

        # Build response with transcript and summary data
        meetings = []
        for meeting, word_count, speaker_count, model, prompt_tokens, completion_tokens, total_tokens, generation_time_ms in results:
            # Calculate actual duration if we have start and end times
            actual_duration = None
            if meeting.start_time and meeting.end_time:
                duration_delta = meeting.end_time - meeting.start_time
                actual_duration = int(duration_delta.total_seconds() / 60)  # Convert to minutes

            meeting_dict = {
                "id": meeting.id,
                "meeting_id": meeting.meeting_id,
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "organizer_email": meeting.organizer_email,
                "start_time": meeting.start_time,
                "end_time": meeting.end_time,
                "duration_minutes": actual_duration if actual_duration else meeting.duration_minutes,
                "participant_count": meeting.participant_count,
                "status": meeting.status,
                "has_transcript": meeting.has_transcript,
                "has_summary": meeting.has_summary,
                "has_distribution": meeting.has_distribution,
                "word_count": word_count,
                "speaker_count": speaker_count,
                # Discovery fields
                "discovery_source": meeting.discovery_source,
                "discovered_at": meeting.discovered_at,
                # AI/Summary stats
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "generation_time_ms": generation_time_ms
            }
            meetings.append(MeetingResponse(**meeting_dict))

        return meetings


@router.get("/{meeting_id}", response_model=MeetingDetailResponse)
async def get_meeting(
    meeting_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """
    Get meeting details.

    Args:
        meeting_id: Meeting ID
        db: Database manager

    Returns:
        Meeting details with summary and transcript
    """
    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Get summary
        summary = session.query(Summary).filter_by(meeting_id=meeting_id).first()

        # Get transcript preview (first 500 chars)
        transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()
        transcript_preview = None
        if transcript:
            preview = transcript.vtt_content[:500]
            transcript_preview = preview + "..." if len(transcript.vtt_content) > 500 else preview

        # Get participants
        participants = session.query(MeetingParticipant).filter_by(meeting_id=meeting_id).all()

        return MeetingDetailResponse(
            **MeetingResponse.from_orm(meeting).dict(),
            summary_text=summary.summary_text if summary else None,
            transcript_preview=transcript_preview,
            participants=[
                {
                    "email": p.email,
                    "display_name": p.display_name,
                    "role": p.role,
                    "is_pilot_user": p.is_pilot_user
                }
                for p in participants
            ]
        )


@router.post("/{meeting_id}/reprocess")
async def reprocess_meeting(
    meeting_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """
    Requeue meeting for processing.

    Args:
        meeting_id: Meeting ID
        db: Database manager

    Returns:
        Success message
    """
    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Enqueue jobs with force_regenerate=True to create new summary version
        queue = JobQueueManager(db)
        job_ids = queue.enqueue_meeting_jobs(meeting_id, priority=10, force_regenerate=True)

        logger.info(f"Reprocessing meeting {meeting_id} with force_regenerate (jobs: {job_ids})")

        return {
            "success": True,
            "message": f"Meeting queued for reprocessing ({len(job_ids)} jobs created)",
            "job_ids": job_ids
        }


@router.post("/{meeting_id}/resend")
async def resend_summary(
    meeting_id: int,
    target: str = Query(..., regex="^(organizer|subscribers|both)$"),
    db: DatabaseManager = Depends(get_db)
):
    """
    Resend meeting summary to specified recipients.

    Args:
        meeting_id: Meeting ID
        target: Who to send to ('organizer', 'subscribers', or 'both')
        db: Database manager

    Returns:
        Success message
    """
    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Check if summary exists
        summary = session.query(Summary).filter_by(meeting_id=meeting_id).first()
        if not summary:
            raise HTTPException(status_code=404, detail="No summary found for this meeting")

        # Create a new distribution job with target parameter
        from datetime import datetime
        job = JobQueue(
            job_type="distribute",
            meeting_id=meeting_id,
            input_data={
                "meeting_id": meeting_id,
                "resend_target": target  # Special flag for distribution processor
            },
            priority=10,  # High priority
            status="pending",
            created_at=datetime.now()
        )
        session.add(job)
        session.commit()
        job_id = job.id

        logger.info(f"Resending summary for meeting {meeting_id} to {target} (job: {job_id})")

        return {
            "success": True,
            "message": f"Summary queued to resend to {target}",
            "job_id": job_id
        }


@router.get("/{meeting_id}/distribution")
async def get_distribution_details(
    meeting_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """
    Get distribution details for a meeting.

    Args:
        meeting_id: Meeting ID

    Returns:
        List of distribution records
    """
    with db.get_session() as session:
        from ...core.database import Distribution

        distributions = session.query(Distribution).filter_by(
            meeting_id=meeting_id
        ).all()

        return [{
            "id": d.id,
            "recipient_email": d.recipient,
            "recipient_type": d.distribution_type,
            "delivery_method": d.distribution_type,
            "sent_at": d.sent_at.isoformat() if d.sent_at else None,
            "status": d.status
        } for d in distributions]


@router.get("/stats/overview")
async def get_stats(
    db: DatabaseManager = Depends(get_db)
):
    """
    Get dashboard statistics.

    Returns:
        Statistics dictionary
    """
    stats = db.get_dashboard_stats()
    return stats
