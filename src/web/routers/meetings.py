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
    # Job retry info (for transcript fetching progress)
    retry_count: Optional[int] = None
    max_retries: Optional[int] = None
    next_retry_at: Optional[datetime] = None
    # Meeting settings (transcription/recording enabled)
    allow_transcription: Optional[bool] = None
    allow_recording: Optional[bool] = None
    # Call type (groupCall, peerToPeer, unknown)
    call_type: Optional[str] = None

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

        # Subquery to get the latest fetch_transcript job for each meeting
        # (for showing retry progress)
        latest_transcript_job = session.query(
            JobQueue.meeting_id,
            func.max(JobQueue.id).label('max_job_id')
        ).filter(
            JobQueue.job_type == "fetch_transcript"
        ).group_by(JobQueue.meeting_id).subquery()

        # Alias for JobQueue to join with the subquery
        TranscriptJob = JobQueue

        # Join with transcript and LATEST summary to get word_count, speaker_count, and AI stats
        query = session.query(
            Meeting,
            Transcript.word_count,
            Transcript.speaker_count,
            Summary.model,
            Summary.prompt_tokens,
            Summary.completion_tokens,
            Summary.total_tokens,
            Summary.generation_time_ms,
            TranscriptJob.retry_count,
            TranscriptJob.max_retries,
            TranscriptJob.next_retry_at
        ).outerjoin(
            Transcript, Meeting.id == Transcript.meeting_id
        ).outerjoin(
            latest_summary_version,
            Meeting.id == latest_summary_version.c.meeting_id
        ).outerjoin(
            Summary,
            (Meeting.id == Summary.meeting_id) &
            (Summary.version == latest_summary_version.c.max_version)
        ).outerjoin(
            latest_transcript_job,
            Meeting.id == latest_transcript_job.c.meeting_id
        ).outerjoin(
            TranscriptJob,
            (TranscriptJob.id == latest_transcript_job.c.max_job_id)
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
        for meeting, word_count, speaker_count, model, prompt_tokens, completion_tokens, total_tokens, generation_time_ms, retry_count, max_retries, next_retry_at in results:
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
                "generation_time_ms": generation_time_ms,
                # Job retry info
                "retry_count": retry_count,
                "max_retries": max_retries,
                "next_retry_at": next_retry_at,
                # Meeting settings
                "allow_transcription": meeting.allow_transcription,
                "allow_recording": meeting.allow_recording,
                "call_type": meeting.call_type
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
            created_at=datetime.utcnow()
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


@router.post("/{meeting_id}/process")
async def process_transcript_only_meeting(
    meeting_id: int,
    db: DatabaseManager = Depends(get_db)
):
    """
    Manually trigger summary generation for a transcript-only meeting.

    This is used for meetings that were captured but not auto-processed
    (no opted-in participants at the time).

    Args:
        meeting_id: Meeting ID

    Returns:
        Success message with job IDs
    """
    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Check if transcript exists
        transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()

        if transcript:
            # Transcript exists - create summary job directly
            summary_job = JobQueue(
                job_type="generate_summary",
                meeting_id=meeting_id,
                input_data={"meeting_id": meeting_id},
                priority=10,  # High priority for manual requests
                max_retries=3
            )
            session.add(summary_job)
            session.flush()

            # Distribution job depends on summary
            distribute_job = JobQueue(
                job_type="distribute",
                meeting_id=meeting_id,
                input_data={"meeting_id": meeting_id},
                priority=10,
                depends_on_job_id=summary_job.id,
                max_retries=5
            )
            session.add(distribute_job)

            # Update meeting status
            meeting.status = "processing"
            session.commit()

            logger.info(f"Manual processing triggered for meeting {meeting_id} (has transcript)")

            return {
                "success": True,
                "message": "Summary generation started",
                "job_ids": [summary_job.id, distribute_job.id]
            }
        else:
            # No transcript - create fetch_transcript job with auto_process=True
            fetch_job = JobQueue(
                job_type="fetch_transcript",
                meeting_id=meeting_id,
                input_data={
                    "meeting_id": meeting_id,
                    "auto_process": True  # Force auto-processing
                },
                priority=10,
                max_retries=3
            )
            session.add(fetch_job)

            # Update meeting status
            meeting.status = "queued"
            session.commit()

            logger.info(f"Manual processing triggered for meeting {meeting_id} (fetching transcript)")

            return {
                "success": True,
                "message": "Transcript fetch and processing started",
                "job_ids": [fetch_job.id]
            }


@router.post("/bulk-process")
async def bulk_process_transcript_only(
    db: DatabaseManager = Depends(get_db)
):
    """
    Bulk process all transcript-only meetings.

    Finds all meetings with status='transcript_only' and triggers processing for each.

    Returns:
        Count of meetings queued for processing
    """
    with db.get_session() as session:
        # Find all transcript-only meetings
        transcript_only_meetings = session.query(Meeting).filter(
            Meeting.status == "transcript_only",
            Meeting.has_transcript == True
        ).all()

        if not transcript_only_meetings:
            return {
                "success": True,
                "message": "No transcript-only meetings found",
                "processed_count": 0
            }

        job_ids = []
        skipped_count = 0
        for meeting in transcript_only_meetings:
            # Check for existing pending/running jobs to avoid duplicates
            existing_job = session.query(JobQueue).filter(
                JobQueue.meeting_id == meeting.id,
                JobQueue.job_type == "generate_summary",
                JobQueue.status.in_(["pending", "running", "retrying"])
            ).first()

            if existing_job:
                logger.info(f"Skipping meeting {meeting.id} - already has pending job {existing_job.id}")
                skipped_count += 1
                continue

            # Create summary job
            summary_job = JobQueue(
                job_type="generate_summary",
                meeting_id=meeting.id,
                input_data={"meeting_id": meeting.id},
                priority=5,  # Normal priority for bulk
                max_retries=3
            )
            session.add(summary_job)
            session.flush()

            # Distribution job depends on summary
            distribute_job = JobQueue(
                job_type="distribute",
                meeting_id=meeting.id,
                input_data={"meeting_id": meeting.id},
                priority=5,
                depends_on_job_id=summary_job.id,
                max_retries=5
            )
            session.add(distribute_job)

            # Update meeting status
            meeting.status = "processing"
            job_ids.append(summary_job.id)

        session.commit()

        processed_count = len(job_ids)
        logger.info(f"Bulk processing triggered for {processed_count} transcript-only meetings (skipped {skipped_count} with existing jobs)")

        message = f"Processing started for {processed_count} meetings"
        if skipped_count > 0:
            message += f" ({skipped_count} skipped - already have pending jobs)"

        return {
            "success": True,
            "message": message,
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "job_ids": job_ids
        }


class SendToEmailRequest(BaseModel):
    """Request body for send-to endpoint."""
    email: str
    include_transcript: bool = False


@router.post("/{meeting_id}/send-to")
async def send_summary_to_email(
    meeting_id: int,
    request: SendToEmailRequest,
    db: DatabaseManager = Depends(get_db)
):
    """
    Send meeting summary to an arbitrary email address.

    This bypasses the opt-in check and sends directly to the specified email.
    Useful for sharing summaries with people who weren't in the meeting.

    Args:
        meeting_id: Meeting ID
        request: Request body with email address and options

    Returns:
        Success message with job ID
    """
    email = request.email.strip().lower()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    with db.get_session() as session:
        meeting = session.query(Meeting).filter_by(id=meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Check if summary exists
        summary = session.query(Summary).filter_by(meeting_id=meeting_id).first()

        # Check if transcript exists
        transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()

        # Must have at least one of summary or transcript
        if not summary and not transcript:
            raise HTTPException(status_code=404, detail="No summary or transcript found for this meeting.")

        # If no summary but transcript exists, force include_transcript
        if not summary and transcript:
            request.include_transcript = True

        # Create distribution job targeting specific email
        job = JobQueue(
            job_type="distribute",
            meeting_id=meeting_id,
            input_data={
                "meeting_id": meeting_id,
                "send_to_email": email,  # Special flag for single recipient
                "bypass_opt_in": True,  # Skip preference check
                "include_transcript": request.include_transcript  # Attach transcript to email
            },
            priority=10,
            status="pending",
            max_retries=3
        )
        session.add(job)
        session.commit()
        job_id = job.id

        content_type = "summary and transcript" if request.include_transcript else "summary"
        logger.info(f"Sending {content_type} for meeting {meeting_id} to {email} (job: {job_id})")

        return {
            "success": True,
            "message": f"Meeting {content_type} queued to send to {email}",
            "job_id": job_id
        }
