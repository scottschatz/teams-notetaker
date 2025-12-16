"""
Transcript Processor

Fetches meeting transcripts from Microsoft Graph API and parses VTT content.
First processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
from typing import Dict, Any
from datetime import datetime, timedelta

from ..processors.base import BaseProcessor, register_processor
from ...graph.client import GraphAPIClient
from ...graph.transcripts import TranscriptFetcher
from ...utils.vtt_parser import parse_vtt, get_transcript_metadata, format_transcript_for_summary
from ...core.database import Transcript, Meeting, ProcessedChatMessage
from ...core.exceptions import TranscriptNotFoundError, GraphAPIError
from ...chat.command_parser import ChatCommandParser, CommandType
from ...preferences.user_preferences import PreferenceManager


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

        # Initialize command parser and preference manager (for opt-in/opt-out system)
        self.command_parser = ChatCommandParser()
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
            # Fetch transcript using new getAllTranscripts approach
            # We use find_transcript_by_thread_id since we have calendar data
            self._log_progress(
                job,
                f"Searching for transcript by organizer {meeting.organizer_name} ({organizer_user_id})"
            )

            # Try to find transcript by matching meeting start time
            # (Calendar meeting IDs don't match transcript meeting IDs)
            transcript_metadata = None
            user_id_for_transcript = organizer_user_id

            try:
                transcript_metadata = self.transcript_fetcher.find_transcript_by_time(
                    organizer_user_id=organizer_user_id,
                    meeting_start_time=meeting.start_time,
                    tolerance_minutes=30
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
                                transcript_metadata = self.transcript_fetcher.find_transcript_by_time(
                                    organizer_user_id=pilot_user_id,
                                    meeting_start_time=meeting.start_time,
                                    tolerance_minutes=30
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
            vtt_content = self.transcript_fetcher.download_transcript_content(
                organizer_user_id=user_id_for_transcript,
                meeting_id=transcript_metadata.get('meetingId'),
                transcript_id=transcript_metadata.get('id')
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
                recording_sharepoint_url = self.transcript_fetcher.get_recording_sharepoint_url(
                    organizer_user_id=user_id_for_transcript,
                    meeting_id=transcript_metadata.get('meetingId')
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

                # Update meeting with recording URL
                meeting.has_transcript = True
                meeting.status = "processing"
                if recording_sharepoint_url:
                    meeting.recording_sharepoint_url = recording_sharepoint_url

                session.commit()

            self._log_progress(job, f"✓ Transcript saved to database (id: {transcript_id})")

            # Check chat for preference commands (opt-in/opt-out system)
            await self._check_chat_for_commands(meeting)

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

    # ========================================================================
    # COMMAND CHECKING (OPT-IN/OPT-OUT SYSTEM)
    # ========================================================================

    async def _check_chat_for_commands(self, meeting: Meeting):
        """
        Check meeting chat for preference commands.

        Called after transcript is fetched, checks chat messages from last 48 hours
        for opt-in/opt-out commands and processes them silently.

        Args:
            meeting: Meeting object with chat_id
        """
        if not meeting.chat_id:
            logger.debug(f"No chat_id for meeting {meeting.id}, skipping command check")
            return

        try:
            logger.info(f"Checking chat messages for commands in meeting {meeting.id}")

            # Get recent messages from chat (last 48 hours to catch commands)
            since = datetime.now() - timedelta(hours=48)

            # Get chat messages using Graph API
            messages = self._get_chat_messages(meeting.chat_id, since)

            if not messages:
                logger.debug(f"No chat messages found for meeting {meeting.id}")
                return

            logger.info(f"Found {len(messages)} chat messages to check for commands")

            commands_processed = 0

            for message in messages:
                message_id = message.get("id")
                if not message_id:
                    continue

                # Skip if already processed
                if self._is_command_processed(message_id):
                    continue

                # Extract message details
                body = message.get("body", {})
                message_text = body.get("content", "")

                # Skip empty messages
                if not message_text or not message_text.strip():
                    continue

                sender = message.get("from", {})
                user = sender.get("user", {})
                user_email = user.get("userPrincipalName", "")
                user_name = user.get("displayName", "Unknown")

                # Skip messages with no user info
                if not user_email:
                    continue

                # Parse command
                command = self.command_parser.parse_command(
                    message_text=message_text,
                    message_id=message_id,
                    chat_id=meeting.chat_id,
                    user_email=user_email,
                    user_name=user_name
                )

                if command and command.is_valid:
                    # Process command immediately (inline, not queued)
                    await self._process_command_inline(command, meeting)
                    commands_processed += 1

                    # Mark as processed
                    self._mark_command_processed(
                        message_id,
                        meeting.chat_id,
                        command.command_type.value
                    )

            if commands_processed > 0:
                logger.info(
                    f"Processed {commands_processed} preference command(s) for meeting {meeting.id}"
                )

        except Exception as e:
            logger.error(f"Error checking chat for commands: {e}", exc_info=True)
            # Don't fail the whole job if command checking fails
            pass

    def _get_chat_messages(self, chat_id: str, since: datetime) -> list:
        """
        Get chat messages from Teams chat.

        Args:
            chat_id: Teams chat thread ID
            since: Only get messages after this datetime

        Returns:
            List of message objects from Graph API
        """
        try:
            # Use Graph API to get chat messages
            endpoint = f"/chats/{chat_id}/messages"

            # Get messages (Graph API returns newest first)
            messages = []
            response = self.graph_client.get(endpoint)

            if response and "value" in response:
                for msg in response["value"]:
                    # Check message timestamp
                    created_str = msg.get("createdDateTime", "")
                    if created_str:
                        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        if created_dt >= since:
                            messages.append(msg)

            return messages

        except Exception as e:
            logger.error(f"Error getting chat messages for {chat_id}: {e}", exc_info=True)
            return []

    async def _process_command_inline(self, command, meeting: Meeting):
        """
        Process command immediately without queuing.

        Handles preference commands silently (no chat confirmations).

        Args:
            command: Parsed Command object
            meeting: Meeting object
        """
        try:
            if command.command_type == CommandType.NO_EMAILS:
                # Per-meeting opt-out
                self.pref_manager.set_meeting_preference(
                    email=command.user_email,
                    meeting_id=meeting.id,
                    receive_emails=False,
                    updated_by="user"
                )
                logger.info(
                    f"User {command.user_email} opted out of meeting {meeting.id}"
                )

            elif command.command_type == CommandType.NO_EMAILS_GLOBAL:
                # Global opt-out
                self.pref_manager.set_user_preference(
                    email=command.user_email,
                    receive_emails=False,
                    updated_by="user"
                )
                logger.info(
                    f"User {command.user_email} globally opted out"
                )

            elif command.command_type == CommandType.ENABLE_EMAILS:
                # Global opt-in
                self.pref_manager.set_user_preference(
                    email=command.user_email,
                    receive_emails=True,
                    updated_by="user"
                )
                logger.info(
                    f"User {command.user_email} globally opted in"
                )

            elif command.command_type == CommandType.DISABLE_DISTRIBUTION:
                # Organizer disables distribution
                if command.user_email.lower() == meeting.organizer_email.lower():
                    with self.db.get_session() as session:
                        db_meeting = session.query(Meeting).filter_by(id=meeting.id).first()
                        if db_meeting:
                            db_meeting.distribution_enabled = False
                            db_meeting.distribution_disabled_by = command.user_email
                            db_meeting.distribution_disabled_at = datetime.now()
                            session.commit()
                    logger.info(
                        f"Organizer {command.user_email} disabled distribution "
                        f"for meeting {meeting.id}"
                    )
                else:
                    logger.warning(
                        f"Non-organizer {command.user_email} tried to disable distribution "
                        f"for meeting {meeting.id} (organizer: {meeting.organizer_email})"
                    )

            elif command.command_type == CommandType.ENABLE_DISTRIBUTION:
                # Organizer re-enables distribution
                if command.user_email.lower() == meeting.organizer_email.lower():
                    with self.db.get_session() as session:
                        db_meeting = session.query(Meeting).filter_by(id=meeting.id).first()
                        if db_meeting:
                            db_meeting.distribution_enabled = True
                            db_meeting.distribution_disabled_by = None
                            db_meeting.distribution_disabled_at = None
                            session.commit()
                    logger.info(
                        f"Organizer {command.user_email} re-enabled distribution "
                        f"for meeting {meeting.id}"
                    )
                else:
                    logger.warning(
                        f"Non-organizer {command.user_email} tried to enable distribution "
                        f"for meeting {meeting.id} (organizer: {meeting.organizer_email})"
                    )

        except Exception as e:
            logger.error(f"Error processing command inline: {e}", exc_info=True)

    def _is_command_processed(self, message_id: str) -> bool:
        """
        Check if chat message has already been processed.

        Args:
            message_id: Teams message ID

        Returns:
            True if already processed, False otherwise
        """
        try:
            with self.db.get_session() as session:
                exists = session.query(ProcessedChatMessage).filter_by(
                    message_id=message_id
                ).first()
                return exists is not None
        except Exception as e:
            logger.error(f"Error checking if message processed: {e}")
            return False

    def _mark_command_processed(
        self,
        message_id: str,
        chat_id: str,
        command_type: str
    ):
        """
        Mark chat message as processed to prevent duplicate processing.

        Args:
            message_id: Teams message ID
            chat_id: Teams chat thread ID
            command_type: Type of command that was processed
        """
        try:
            with self.db.get_session() as session:
                processed = ProcessedChatMessage(
                    message_id=message_id,
                    chat_id=chat_id,
                    command_type=command_type,
                    result="success",
                    processed_at=datetime.now()
                )
                session.add(processed)
                session.commit()
                logger.debug(f"Marked message {message_id} as processed ({command_type})")
        except Exception as e:
            logger.error(f"Error marking message as processed: {e}", exc_info=True)
