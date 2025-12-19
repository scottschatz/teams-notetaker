"""
Transcript Processor

Fetches meeting transcripts from Microsoft Graph API and parses VTT content.
First processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
import asyncio
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from ..processors.base import BaseProcessor, register_processor
from ...graph.client import GraphAPIClient
from ...graph.transcripts import TranscriptFetcher
from ...utils.vtt_parser import parse_vtt, get_transcript_metadata, format_transcript_for_summary
from ...core.database import Transcript, Meeting, JobQueue
from ...core.exceptions import TranscriptNotFoundError, GraphAPIError
from ...preferences.user_preferences import PreferenceManager


logger = logging.getLogger(__name__)

# Maximum VTT transcript size to prevent OOM on very long meetings
MAX_VTT_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


@register_processor("fetch_transcript")
class TranscriptProcessor(BaseProcessor):
    """
    Fetches and parses meeting transcripts.

    Input (job.input_data):
        - meeting_id: Database meeting ID

    Output (job.output_data):
        - success: bool
        - transcript_id: Database transcript ID
        - vtt_url: URL where VTT was downloaded from
        - transcript_sharepoint_url: SharePoint URL for transcript (respects permissions)
        - recording_sharepoint_url: SharePoint URL for recording (if available)
        - speaker_count: Number of unique speakers
        - word_count: Total word count
        - duration_seconds: Transcript duration
        - segment_count: Number of parsed segments
        - message: Status message

    Updates:
        - meetings.has_transcript = True
        - meetings.status = 'processing'
        - meetings.recording_sharepoint_url (if recording available)
        - Creates transcript record in database with SharePoint URLs

    Errors:
        - TranscriptNotFoundError: No transcript available
        - GraphAPIError: API request failed
        - VTTParseError: VTT parsing failed
    """

    def __init__(self, db, config):
        """
        Initialize transcript processor.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
        """
        super().__init__(db, config)

        # Initialize Graph API client (use beta for getAllTranscripts)
        self.graph_client = GraphAPIClient(config.graph_api, use_beta=True)
        self.transcript_fetcher = TranscriptFetcher(self.graph_client)

        # Initialize preference manager (for opt-in/opt-out system)
        self.pref_manager = PreferenceManager(db)

    async def process(self, job) -> Dict[str, Any]:
        """
        Process fetch_transcript job.

        Args:
            job: JobQueue object

        Returns:
            Output data dictionary
        """
        # Validate input
        self._validate_job_input(job, required_fields=["meeting_id"])

        meeting_id = job.input_data["meeting_id"]

        self._log_progress(job, f"Fetching transcript for meeting {meeting_id}")

        # Get meeting from database
        meeting = self._get_meeting(meeting_id)

        # Check if transcript already exists
        with self.db.get_session() as session:
            existing = session.query(Transcript).filter_by(meeting_id=meeting_id).first()
            if existing:
                self._log_progress(job, "Transcript already exists in database, skipping", "warning")
                return self._create_output_data(
                    success=True,
                    message="Transcript already exists",
                    transcript_id=existing.id,
                    cached=True
                )

        # Get organizer user ID (required for getAllTranscripts API)
        organizer_user_id = meeting.organizer_user_id
        if not organizer_user_id:
            raise GraphAPIError(
                f"Meeting organizer user ID not found for {meeting.organizer_email}. "
                "This may be an old meeting record - try rediscovering it."
            )

        try:
            loop = asyncio.get_event_loop()
            transcript_metadata = None
            user_id_for_transcript = organizer_user_id

            # Check if transcript_id was provided directly (from webhook notification)
            # or stored on the meeting record (from webhook arriving after backfill)
            # This is more reliable than time-based matching
            provided_transcript_id = (
                job.input_data.get("transcript_id") or
                meeting.graph_transcript_id  # Fallback: stored from webhook
            )
            if provided_transcript_id:
                source = "job input" if job.input_data.get("transcript_id") else "meeting record"
                self._log_progress(
                    job,
                    f"Using transcript_id from {source} (skipping time-based search)"
                )

                # Use online_meeting_id from multiple sources (in priority order):
                # 1. job.input_data (from webhook notification)
                # 2. meeting.online_meeting_id (new explicit column)
                # 3. meeting.meeting_id (legacy - may be calendar event ID for old meetings)
                online_meeting_id = (
                    job.input_data.get("online_meeting_id") or
                    meeting.online_meeting_id or  # NEW: explicit column
                    meeting.meeting_id
                )

                # Validate: calendar event IDs (AAMk...) won't work for transcript API
                if online_meeting_id and online_meeting_id.startswith("AAMk"):
                    logger.warning(
                        f"Meeting {meeting.id} has calendar event ID format (AAMk...) "
                        f"instead of online meeting ID (MSp...). Transcript fetch may fail."
                    )

                # We have transcript_id - construct the transcript metadata
                transcript_metadata = {
                    'id': provided_transcript_id,
                    'meetingId': online_meeting_id,
                    'createdDateTime': None  # Not needed when we have the ID
                }
            else:
                # No transcript_id provided - fall back to time-based matching
                self._log_progress(
                    job,
                    f"Searching for transcript by organizer {meeting.organizer_name} ({organizer_user_id})"
                )

            # If we don't have transcript metadata yet, search by time
            if not transcript_metadata:
                try:
                    # Run in executor to avoid blocking event loop
                    transcript_metadata = await loop.run_in_executor(
                        None,
                        lambda: self.transcript_fetcher.find_transcript_by_time(
                            organizer_user_id=organizer_user_id,
                            meeting_start_time=meeting.start_time,
                            tolerance_minutes=30
                        )
                    )
                except Exception as e:
                    # If 403 error accessing organizer's transcripts, try using a pilot user's ID
                    if "403" in str(e) or "not allowed" in str(e).lower():
                        self._log_progress(
                            job,
                            f"Cannot access organizer's transcripts (403), trying pilot user fallback",
                            "warning"
                        )

                        # Get a pilot user ID as fallback (someone who attended the meeting)
                        with self.db.get_session() as session:
                            from src.core.database import MeetingParticipant
                            pilot_participant = session.query(MeetingParticipant).filter(
                                MeetingParticipant.meeting_id == meeting_id,
                                MeetingParticipant.is_pilot_user == True,
                                MeetingParticipant.email != meeting.organizer_email
                            ).first()

                            if pilot_participant:
                                # Get user ID for pilot participant
                                from src.graph.meetings import MeetingDiscovery
                                discovery = MeetingDiscovery(self.graph_client)
                                pilot_user_id = discovery._get_user_id(pilot_participant.email)

                                if pilot_user_id:
                                    self._log_progress(
                                        job,
                                        f"Trying to access transcript via pilot user {pilot_participant.email}",
                                        "info"
                                    )
                                    # Run in executor to avoid blocking event loop
                                    transcript_metadata = await loop.run_in_executor(
                                        None,
                                        lambda: self.transcript_fetcher.find_transcript_by_time(
                                            organizer_user_id=pilot_user_id,
                                            meeting_start_time=meeting.start_time,
                                            tolerance_minutes=30
                                        )
                                    )
                                    user_id_for_transcript = pilot_user_id
                                else:
                                    raise e
                            else:
                                raise e
                    else:
                        raise e

            if not transcript_metadata:
                # No transcript found
                raise TranscriptNotFoundError(
                    f"No transcript found for meeting organized by {meeting.organizer_name}"
                )

            # Download the transcript content (for AI processing)
            # Use the user_id_for_transcript (might be organizer or pilot user fallback)
            # Run in executor to avoid blocking event loop
            vtt_content = await loop.run_in_executor(
                None,
                lambda: self.transcript_fetcher.download_transcript_content(
                    organizer_user_id=user_id_for_transcript,
                    meeting_id=transcript_metadata.get('meetingId'),
                    transcript_id=transcript_metadata.get('id')
                )
            )

            # Check VTT size to prevent OOM on very long transcripts
            vtt_size_bytes = len(vtt_content.encode('utf-8'))
            if vtt_size_bytes > MAX_VTT_SIZE_BYTES:
                raise TranscriptNotFoundError(
                    f"Transcript too large ({vtt_size_bytes:,} bytes, "
                    f"max {MAX_VTT_SIZE_BYTES:,} bytes). Meeting may be too long to process."
                )

            # Get SharePoint URL for transcript (for secure user access)
            transcript_sharepoint_url = transcript_metadata.get('transcriptContentUrl', '')

            if user_id_for_transcript != organizer_user_id:
                self._log_progress(
                    job,
                    f"Downloaded {len(vtt_content)} chars via pilot user fallback, got SharePoint URL"
                )
            else:
                self._log_progress(
                    job,
                    f"Downloaded {len(vtt_content)} chars of VTT content, got SharePoint URL"
                )

            # Get recording SharePoint URL (if available)
            recording_sharepoint_url = None
            try:
                # Run in executor to avoid blocking event loop
                recording_sharepoint_url = await loop.run_in_executor(
                    None,
                    lambda: self.transcript_fetcher.get_recording_sharepoint_url(
                        organizer_user_id=user_id_for_transcript,
                        meeting_id=transcript_metadata.get('meetingId')
                    )
                )
                if recording_sharepoint_url:
                    self._log_progress(job, "✓ Found recording SharePoint URL")
            except Exception as e:
                self._log_progress(job, f"No recording URL available: {e}", "info")

            # Build transcript data structure
            transcript_data = {
                'id': transcript_metadata.get('id'),
                'meetingId': transcript_metadata.get('meetingId'),
                'createdDateTime': transcript_metadata.get('createdDateTime'),
                'contentUrl': transcript_sharepoint_url,
                'content': vtt_content
            }

            vtt_content = transcript_data["content"]
            vtt_url = transcript_data.get("contentUrl", "")
            graph_transcript_id = transcript_data.get("id", "")

            # Parse VTT content
            self._log_progress(job, "Parsing VTT transcript")

            parsed_segments = parse_vtt(vtt_content)

            if not parsed_segments:
                raise TranscriptNotFoundError("VTT parsing produced no segments")

            # Get metadata
            metadata = get_transcript_metadata(parsed_segments)

            speaker_count = metadata["speaker_count"]
            word_count = metadata["word_count"]
            duration_seconds = metadata["total_duration_seconds"]

            # Extract detailed speaker stats (v2.1 feature)
            from src.utils.transcript_stats import extract_transcript_stats

            detailed_stats = extract_transcript_stats(vtt_content)
            actual_duration_minutes = detailed_stats.get('actual_duration_minutes', 0)
            speaker_details = detailed_stats.get('speakers', [])

            self._log_progress(
                job,
                f"Parsed transcript: {len(parsed_segments)} segments, "
                f"{speaker_count} speakers, {word_count} words, "
                f"{actual_duration_minutes} min actual duration"
            )

            # Save to database
            with self.db.get_session() as session:
                transcript = Transcript(
                    meeting_id=meeting_id,
                    vtt_content=vtt_content,
                    vtt_url=vtt_url,
                    parsed_content=parsed_segments,  # Store as JSONB
                    speaker_count=speaker_count,
                    word_count=word_count,
                    transcript_sharepoint_url=transcript_sharepoint_url  # NEW: SharePoint URL
                )
                session.add(transcript)
                session.flush()

                transcript_id = transcript.id

                # Update meeting (query in THIS session to avoid detached object bug)
                meeting_in_session = session.query(Meeting).filter_by(id=meeting_id).first()
                if meeting_in_session:
                    meeting_in_session.has_transcript = True
                    if recording_sharepoint_url:
                        meeting_in_session.recording_sharepoint_url = recording_sharepoint_url

                # Check auto_process flag from job input_data (default True for backwards compat)
                auto_process = job.input_data.get("auto_process", True) if job.input_data else True

                if auto_process:
                    # Full processing: create summary and distribution jobs
                    if meeting_in_session:
                        meeting_in_session.status = "processing"

                    # Create next jobs in chain: generate_summary -> distribute
                    summary_job = JobQueue(
                        job_type="generate_summary",
                        meeting_id=meeting_id,
                        input_data={"meeting_id": meeting_id},
                        priority=5,
                        max_retries=3
                    )
                    session.add(summary_job)
                    session.flush()  # Get summary_job.id

                    # Distribution job depends on summary completion
                    distribute_job = JobQueue(
                        job_type="distribute",
                        meeting_id=meeting_id,
                        input_data={"meeting_id": meeting_id},
                        priority=5,
                        depends_on_job_id=summary_job.id,
                        max_retries=5
                    )
                    session.add(distribute_job)
                    session.commit()

                    self._log_progress(job, f"✓ Transcript saved, summary job enqueued (id: {transcript_id})")
                else:
                    # Transcript only: no downstream jobs, just capture for RAG
                    if meeting_in_session:
                        meeting_in_session.status = "transcript_only"
                    session.commit()

                    self._log_progress(job, f"✓ Transcript captured (transcript only mode, no auto-processing)")

            return self._create_output_data(
                success=True,
                message=f"Transcript fetched and parsed successfully ({word_count} words, {speaker_count} speakers)",
                transcript_id=transcript_id,
                vtt_url=vtt_url,
                transcript_sharepoint_url=transcript_sharepoint_url,  # NEW: SharePoint URL
                recording_sharepoint_url=recording_sharepoint_url,  # NEW: Recording URL
                speaker_count=speaker_count,
                word_count=word_count,
                duration_seconds=duration_seconds,
                segment_count=len(parsed_segments),
                # v2.1: Detailed speaker stats from transcript
                actual_duration_minutes=actual_duration_minutes,
                speaker_details=speaker_details  # List of {name, duration_minutes, percentage, words}
            )

        except TranscriptNotFoundError as e:
            self._log_progress(job, f"Transcript not available: {e}", "warning")

            # Exponential backoff retry: 15min, 30min, 60min max
            # Transcripts rarely appear after 1 hour, so no point retrying longer
            max_retries = 3  # 15min + 30min + 60min = max 1hr 45min total

            # Handle None retry_count (treat as 0)
            retry_count = job.retry_count if job.retry_count is not None else 0

            if retry_count < max_retries:
                # Calculate next retry time: 15min, 30min, 60min
                delay_minutes = 15 * (2 ** retry_count)
                next_retry = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)

                self._log_progress(
                    job,
                    f"Transcript not ready. Scheduling retry {retry_count + 1}/{max_retries} "
                    f"in {delay_minutes} minutes (at {next_retry})"
                )

                # Update job for retry
                with self.db.get_session() as session:
                    db_job = session.query(JobQueue).filter_by(id=job.id).first()
                    if db_job:
                        db_job.status = "retrying"
                        db_job.retry_count = retry_count + 1
                        db_job.next_retry_at = next_retry
                        db_job.error_message = f"Transcript not ready: {e}"
                        session.commit()

                # Don't update meeting status yet - still trying
                return self._create_output_data(
                    success=False,
                    message=f"Transcript not ready, retry scheduled for {next_retry}",
                    retry_scheduled=True,
                    next_retry_at=next_retry.isoformat(),
                    retry_count=retry_count + 1,
                    max_retries=max_retries
                )
            else:
                # Max retries reached - give up
                total_minutes = sum(15 * (2 ** i) for i in range(max_retries))
                hours = total_minutes / 60

                self._log_progress(
                    job,
                    f"Max retries ({max_retries}) reached. Transcript still not available after {hours:.1f} hours.",
                    "error"
                )

                # Update meeting status to failed
                self._update_meeting_status(
                    meeting_id,
                    "failed",
                    error_message=f"Transcript not available after {max_retries} retries ({hours:.1f} hours)"
                )

                return self._create_output_data(
                    success=False,
                    message=f"Transcript not available after {max_retries} retries",
                    skipped=True,
                    max_retries_reached=True
                )

        except Exception as e:
            self._log_progress(job, f"Failed to fetch transcript: {e}", "error")
            raise

