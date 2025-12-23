"""
Transcript Processor

Fetches meeting transcripts from Microsoft Graph API and parses VTT content.
First processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
import asyncio
from typing import Dict, Any
from datetime import datetime, timedelta

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

            # Validate transcript completeness against call record duration
            # This catches transcripts fetched before Microsoft finished processing
            # (which happened with Meeting 809 - we got 10 min of a 47 min call)
            expected_duration_seconds = None
            if meeting.end_time and meeting.start_time:
                expected_duration_seconds = (meeting.end_time - meeting.start_time).total_seconds()
            elif meeting.duration_minutes:
                expected_duration_seconds = meeting.duration_minutes * 60

            if expected_duration_seconds and expected_duration_seconds > 60:
                # Only validate if meeting was > 1 minute (short calls may be accurate)
                # Allow 20% tolerance for normal variation
                MIN_TRANSCRIPT_RATIO = 0.8
                transcript_ratio = duration_seconds / expected_duration_seconds

                if transcript_ratio < MIN_TRANSCRIPT_RATIO:
                    shortfall_pct = (1 - transcript_ratio) * 100
                    self._log_progress(
                        job,
                        f"⚠️ Transcript may be incomplete: meeting was {expected_duration_seconds:.0f}s "
                        f"({expected_duration_seconds/60:.1f} min), transcript is only {duration_seconds:.0f}s "
                        f"({duration_seconds/60:.1f} min) - missing ~{shortfall_pct:.0f}%",
                        "warning"
                    )
                    # Note: We still save the transcript but log the warning.
                    # A future enhancement could trigger automatic re-fetch after a delay.

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
                    # If we successfully fetched a transcript, recording was definitely enabled
                    meeting_in_session.recording_started = True
                    meeting_in_session.transcript_available = True
                    if recording_sharepoint_url:
                        meeting_in_session.recording_sharepoint_url = recording_sharepoint_url

                # Mark transcript speakers as attended
                # The call record sessions don't always match who actually spoke
                # This ensures the email shows correct "Top Speakers" list
                if speaker_details:
                    speaker_names = [s['name'].lower() for s in speaker_details if s.get('name') and s['name'] != 'Unknown']
                    if speaker_names:
                        from sqlalchemy import func
                        from src.core.database import MeetingParticipant
                        # Update attended=True for participants matching speaker names
                        # Use flexible matching: exact or normalized (dots/underscores → spaces)
                        updated = session.query(MeetingParticipant).filter(
                            MeetingParticipant.meeting_id == meeting_id,
                            MeetingParticipant.attended == False,
                            func.lower(func.replace(func.replace(MeetingParticipant.display_name, '.', ' '), '_', ' ')).in_(
                                [name.replace('.', ' ').replace('_', ' ') for name in speaker_names]
                            )
                        ).update({MeetingParticipant.attended: True}, synchronize_session=False)

                        if updated > 0:
                            self._log_progress(job, f"Marked {updated} transcript speakers as attended")

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

            # Check if transcription was enabled for this meeting
            # If explicitly disabled (False), don't waste time retrying
            allow_transcription = meeting.allow_transcription

            # Track chat event signals for retry scheduling
            transcript_available = False  # Transcript event posted = transcript IS READY
            recording_started = False     # Recording event = transcription was enabled

            # FAST PATH: Check chat events if we have chat_id
            # Always check on every retry - transcript_available may change from False to True
            # as the meeting ends and Microsoft processes the transcript
            chat_id = meeting.chat_id
            if chat_id:
                retry_count = job.retry_count if job.retry_count is not None else 0
                self._log_progress(
                    job,
                    f"Checking chat events for transcript/recording status (retry {retry_count})..."
                )
                try:
                    loop = asyncio.get_event_loop()
                    chat_check = await loop.run_in_executor(
                        None,
                        lambda: self.transcript_fetcher.check_transcript_readiness_from_chat(chat_id)
                    )

                    if not chat_check.get("error"):
                        transcript_available = chat_check.get("transcript_available", False)
                        recording_started = chat_check.get("recording_started", False)

                        self._log_progress(
                            job,
                            f"Chat events: transcript_available={transcript_available}, "
                            f"recording_started={recording_started}"
                        )

                        # If transcript event was posted, transcript IS READY NOW
                        if transcript_available:
                            if allow_transcription is None:
                                allow_transcription = True
                            self._log_progress(
                                job,
                                "callTranscriptEventMessageDetail found - transcript IS AVAILABLE, Graph API may just be slow"
                            )
                        # If recording started but no transcript event yet
                        elif recording_started:
                            if allow_transcription is None:
                                allow_transcription = True
                            self._log_progress(
                                job,
                                "Recording started - transcription enabled, transcript not yet ready"
                            )
                        # No events = transcription was never enabled
                        elif allow_transcription is None:
                            allow_transcription = False
                            self._log_progress(
                                job,
                                "No recording/transcript events found - transcription was NOT enabled",
                                "warning"
                            )

                        # Store chat event signals on the meeting record
                        with self.db.get_session() as session:
                            db_meeting = session.query(Meeting).filter_by(id=meeting_id).first()
                            if db_meeting:
                                # Always update these - they may change between retries
                                db_meeting.recording_started = recording_started
                                db_meeting.transcript_available = transcript_available
                                # Only set allow_transcription if not already known
                                if db_meeting.allow_transcription is None and allow_transcription is not None:
                                    db_meeting.allow_transcription = allow_transcription
                                session.commit()
                    else:
                        self._log_progress(
                            job,
                            f"Chat events check returned error: {chat_check.get('error')}",
                            "warning"
                        )
                except Exception as chat_err:
                    self._log_progress(job, f"Could not check chat events: {chat_err}", "warning")
            else:
                self._log_progress(job, "No chat_id available - cannot check chat events", "info")

            # If we still don't know, try to check via Graph API (onlineMeeting)
            if allow_transcription is None:
                self._log_progress(job, "Checking if transcription was enabled for this meeting...")
                try:
                    allow_transcription = self._check_transcription_enabled(meeting)
                    # Store the result on the meeting record for future reference
                    with self.db.get_session() as session:
                        db_meeting = session.query(Meeting).filter_by(id=meeting_id).first()
                        if db_meeting:
                            db_meeting.allow_transcription = allow_transcription
                            session.commit()
                            self._log_progress(job, f"Updated meeting.allow_transcription = {allow_transcription}")
                except Exception as check_err:
                    self._log_progress(job, f"Could not check transcription status: {check_err}", "warning")

            # If transcription is explicitly disabled, mark meeting and stop immediately
            if allow_transcription is False:
                self._log_progress(
                    job,
                    "Transcription was NOT enabled for this meeting - no transcript will be available",
                    "warning"
                )
                self._update_meeting_status(
                    meeting_id,
                    "transcription_disabled",
                    error_message="Recording/transcription was not enabled for this meeting"
                )
                return self._create_output_data(
                    success=False,
                    message="Transcription was not enabled for this meeting",
                    transcription_disabled=True,
                    skipped=True
                )

            # Choose retry schedule based on chat event signals
            if transcript_available:
                # Transcript event posted = it's READY, Graph API just needs to catch up
                # Use very aggressive retries - should succeed within seconds to minutes
                retry_delays = [0.5, 1, 1, 2, 3]  # 30s, 1m, 1m, 2m, 3m
                self._log_progress(job, "Using VERY aggressive retry schedule (transcript event seen)")
            elif recording_started:
                # Recording started but no transcript event yet
                # Transcript will be ready soon, use moderate schedule
                retry_delays = [2, 5, 10, 15, 30]  # Minutes for each retry
                self._log_progress(job, "Using moderate retry schedule (recording started)")
            else:
                # No chat events found (or couldn't check) - use conservative schedule
                retry_delays = [5, 10, 15, 30]  # Minutes for each retry

            max_retries = len(retry_delays)

            # Handle None retry_count (treat as 0)
            retry_count = job.retry_count if job.retry_count is not None else 0

            if retry_count < max_retries:
                # Get delay for this retry attempt
                delay_minutes = retry_delays[retry_count]
                # Use UTC-naive datetime for consistency with database storage
                next_retry = datetime.utcnow() + timedelta(minutes=delay_minutes)

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
                # Total wait = 15 min (initial delay) + sum of retry delays
                total_minutes = 15 + sum(retry_delays)

                self._log_progress(
                    job,
                    f"Max retries ({max_retries}) reached. Transcript still not available after {total_minutes} minutes.",
                    "warning"
                )

                # Update meeting status to no_transcript (not failed - recording wasn't enabled)
                self._update_meeting_status(
                    meeting_id,
                    "no_transcript",
                    error_message="Recording/transcription was not enabled for this meeting"
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

    def _check_transcription_enabled(self, meeting: Meeting) -> bool | None:
        """
        Check if transcription was enabled for a meeting via Graph API.

        Uses the onlineMeeting properties to determine if recording/transcription
        was allowed for this meeting.

        Args:
            meeting: Meeting object with organizer_user_id and online_meeting_id

        Returns:
            True if transcription enabled, False if disabled, None if unknown
        """
        if not meeting.organizer_user_id:
            logger.warning(f"Cannot check transcription status - no organizer_user_id for meeting {meeting.id}")
            return None

        # Try to get online meeting ID
        online_meeting_id = meeting.online_meeting_id or meeting.meeting_id
        if not online_meeting_id:
            logger.warning(f"Cannot check transcription status - no online_meeting_id for meeting {meeting.id}")
            return None

        try:
            # If we have a join_url, use filter query
            if meeting.join_url:
                meetings_response = self.graph_client.get(
                    f"/users/{meeting.organizer_user_id}/onlineMeetings",
                    params={"$filter": f"joinWebUrl eq '{meeting.join_url}'"}
                )
                meetings = meetings_response.get("value", [])
                if meetings:
                    meeting_data = meetings[0]
                    allow_transcription = meeting_data.get("allowTranscription")
                    allow_recording = meeting_data.get("allowRecording")
                    logger.info(
                        f"Meeting {meeting.id}: allowTranscription={allow_transcription}, "
                        f"allowRecording={allow_recording}"
                    )
                    # Update allow_recording as well if we got it
                    if allow_recording is not None:
                        with self.db.get_session() as session:
                            db_meeting = session.query(Meeting).filter_by(id=meeting.id).first()
                            if db_meeting:
                                db_meeting.allow_recording = allow_recording
                                session.commit()
                    return allow_transcription
                else:
                    logger.warning(f"No online meeting found for join_url: {meeting.join_url}")
                    return None
            else:
                # Try direct query with online_meeting_id
                meeting_data = self.graph_client.get(
                    f"/users/{meeting.organizer_user_id}/onlineMeetings/{online_meeting_id}"
                )
                allow_transcription = meeting_data.get("allowTranscription")
                allow_recording = meeting_data.get("allowRecording")
                logger.info(
                    f"Meeting {meeting.id}: allowTranscription={allow_transcription}, "
                    f"allowRecording={allow_recording}"
                )
                # Update allow_recording as well if we got it
                if allow_recording is not None:
                    with self.db.get_session() as session:
                        db_meeting = session.query(Meeting).filter_by(id=meeting.id).first()
                        if db_meeting:
                            db_meeting.allow_recording = allow_recording
                            session.commit()
                return allow_transcription

        except Exception as e:
            logger.warning(f"Failed to check transcription status for meeting {meeting.id}: {e}")
            return None

