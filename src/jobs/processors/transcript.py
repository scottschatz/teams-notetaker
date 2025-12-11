"""
Transcript Processor

Fetches meeting transcripts from Microsoft Graph API and parses VTT content.
First processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
from typing import Dict, Any
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...graph.client import GraphAPIClient
from ...graph.transcripts import TranscriptFetcher
from ...utils.vtt_parser import parse_vtt, get_transcript_metadata, format_transcript_for_summary
from ...core.database import Transcript, MeetingStatus
from ...core.exceptions import TranscriptNotFoundError, GraphAPIError


logger = logging.getLogger(__name__)


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
        - speaker_count: Number of unique speakers
        - word_count: Total word count
        - duration_seconds: Transcript duration
        - message: Status message

    Updates:
        - meetings.has_transcript = True
        - meetings.status = 'processing'
        - Creates transcript record in database

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
            # Fetch transcript using new getAllTranscripts approach
            # We use find_transcript_by_thread_id since we have calendar data
            self._log_progress(
                job,
                f"Searching for transcript by organizer {meeting.organizer_name} ({organizer_user_id})"
            )

            # Extract thread ID from meeting ID or joinUrl
            # Meeting IDs from getAllTranscripts contain the thread ID
            # We'll search recent transcripts and match by time or meeting ID

            transcript_data = self.transcript_fetcher.get_transcript_with_metadata(
                organizer_user_id=organizer_user_id,
                meeting_id=meeting.meeting_id  # Try with calendar event ID first
            )

            if not transcript_data:
                # No transcript found
                raise TranscriptNotFoundError(
                    f"No transcript found for meeting organized by {meeting.organizer_name}"
                )

            vtt_content = transcript_data["content"]
            vtt_url = transcript_data.get("contentUrl", "")
            graph_transcript_id = transcript_data.get("id", "")

            self._log_progress(job, f"Downloaded {len(vtt_content)} chars of VTT content")

            # Parse VTT content
            self._log_progress(job, "Parsing VTT transcript")

            parsed_segments = parse_vtt(vtt_content)

            if not parsed_segments:
                raise TranscriptNotFoundError("VTT parsing produced no segments")

            # Get metadata
            metadata = get_transcript_metadata(parsed_segments)

            speaker_count = metadata["speaker_count"]
            word_count = metadata["word_count"]
            duration_seconds = metadata["duration_seconds"]

            self._log_progress(
                job,
                f"Parsed transcript: {len(parsed_segments)} segments, "
                f"{speaker_count} speakers, {word_count} words, "
                f"{duration_seconds}s duration"
            )

            # Save to database
            with self.db.get_session() as session:
                transcript = Transcript(
                    meeting_id=meeting_id,
                    vtt_content=vtt_content,
                    vtt_url=vtt_url,
                    parsed_content=parsed_segments,  # Store as JSONB
                    speaker_count=speaker_count,
                    word_count=word_count
                )
                session.add(transcript)
                session.flush()

                transcript_id = transcript.id

                # Update meeting
                meeting.has_transcript = True
                meeting.status = "processing"

                session.commit()

            self._log_progress(job, f"✓ Transcript saved to database (id: {transcript_id})")

            return self._create_output_data(
                success=True,
                message=f"Transcript fetched and parsed successfully ({word_count} words, {speaker_count} speakers)",
                transcript_id=transcript_id,
                vtt_url=vtt_url,
                speaker_count=speaker_count,
                word_count=word_count,
                duration_seconds=duration_seconds,
                segment_count=len(parsed_segments)
            )

        except TranscriptNotFoundError as e:
            self._log_progress(job, f"Transcript not available: {e}", "warning")

            # Update meeting status
            self._update_meeting_status(
                meeting_id,
                "skipped",
                error_message=f"No transcript available: {e}"
            )

            return self._create_output_data(
                success=False,
                message=f"Transcript not available: {e}",
                skipped=True
            )

        except Exception as e:
            self._log_progress(job, f"Failed to fetch transcript: {e}", "error")
            raise
