"""
Chat Command Processor

Processes bot commands from Teams chat messages.
Handles email requests, preference updates, and re-summarization.
"""

import logging
from typing import Dict, Any
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...graph.mail import EmailSender
from ...graph.chat import TeamsChatPoster
from ...graph.client import GraphAPIClient
from ...preferences import PreferenceManager
from ...core.database import Summary, Meeting
from ...core.exceptions import (
    ChatCommandError,
    EmailSendError,
    TeamsChatPostError,
    SummaryGenerationError
)
from ...chat import CommandType


logger = logging.getLogger(__name__)


@register_processor("process_chat_command")
class ChatCommandProcessor(BaseProcessor):
    """
    Processes chat commands from Teams meetings.

    Input (job.input_data):
        - command_type: Command type (email_me, email_all, no_emails, summarize_again)
        - meeting_id: Database meeting ID
        - message_id: Teams message ID
        - chat_id: Teams chat thread ID
        - user_email: Email of user who sent command
        - user_name: Display name of user
        - parameters: Command-specific parameters
        - raw_message: Original message text

    Output (job.output_data):
        - success: bool
        - command_type: Command type
        - action_taken: Description of action
        - confirmation_message: Message posted to chat
        - error: Error message if failed

    Errors:
        - ChatCommandError: Command processing failed
        - EmailSendError: Email sending failed
        - TeamsChatPostError: Chat posting failed
    """

    def __init__(self, db, config):
        """
        Initialize chat command processor.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
        """
        super().__init__(db, config)

        # Initialize Graph API components
        self.graph_client = GraphAPIClient(config.graph_api)
        self.email_sender = EmailSender(self.graph_client)
        self.chat_poster = TeamsChatPoster(self.graph_client)

        # Initialize preference manager
        self.pref_manager = PreferenceManager(db)

    async def process(self, job) -> Dict[str, Any]:
        """
        Process chat command job.

        Args:
            job: JobQueue object

        Returns:
            Output data dictionary
        """
        # Validate input
        required_fields = [
            "command_type", "meeting_id", "message_id", "chat_id",
            "user_email", "user_name"
        ]
        self._validate_job_input(job, required_fields=required_fields)

        command_type_str = job.input_data["command_type"]
        meeting_id = job.input_data["meeting_id"]
        message_id = job.input_data["message_id"]
        chat_id = job.input_data["chat_id"]
        user_email = job.input_data["user_email"]
        user_name = job.input_data["user_name"]
        parameters = job.input_data.get("parameters", {})

        self._log_progress(
            job,
            f"Processing {command_type_str} command from {user_email}"
        )

        # Get meeting from database
        meeting = self._get_meeting(meeting_id)

        # Dispatch to specific handler
        try:
            if command_type_str == CommandType.EMAIL_ME.value:
                result = await self._handle_email_me(
                    meeting, user_email, user_name, chat_id, parameters
                )
            elif command_type_str == CommandType.EMAIL_ALL.value:
                result = await self._handle_email_all(
                    meeting, user_email, user_name, chat_id, parameters
                )
            elif command_type_str == CommandType.NO_EMAILS.value:
                result = await self._handle_no_emails(
                    meeting, user_email, user_name, chat_id, parameters
                )
            elif command_type_str == CommandType.SUMMARIZE_AGAIN.value:
                result = await self._handle_summarize_again(
                    meeting, user_email, user_name, chat_id, parameters
                )
            else:
                raise ChatCommandError(f"Unknown command type: {command_type_str}")

            self._log_progress(job, f"✓ {result['action_taken']}")

            return self._create_output_data(
                success=True,
                command_type=command_type_str,
                action_taken=result["action_taken"],
                confirmation_message=result["confirmation_message"]
            )

        except Exception as e:
            error_msg = str(e)
            self._log_progress(job, f"Command failed: {error_msg}", "error")

            # Post error message to chat
            try:
                await self.chat_poster.post_message(
                    chat_id=chat_id,
                    content=f"❌ {error_msg}"
                )
            except Exception as chat_error:
                logger.error(f"Failed to post error message to chat: {chat_error}")

            return self._create_output_data(
                success=False,
                command_type=command_type_str,
                error=error_msg
            )

    async def _handle_email_me(
        self,
        meeting: Meeting,
        user_email: str,
        user_name: str,
        chat_id: str,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle 'email me' command - send personalized email to user.

        Args:
            meeting: Meeting object
            user_email: User email
            user_name: User display name
            chat_id: Chat thread ID
            parameters: Command parameters

        Returns:
            Result dictionary with action_taken and confirmation_message
        """
        logger.info(f"Handling email_me command for {user_email}")

        # Get latest summary for meeting
        with self.db.get_session() as session:
            summary = session.query(Summary).filter_by(
                meeting_id=meeting.id
            ).order_by(
                Summary.version.desc()
            ).first()

            if not summary:
                raise ChatCommandError(
                    "No summary available yet. Please wait for processing to complete."
                )

            # Prepare meeting metadata
            meeting_metadata = {
                "meeting_id": meeting.id,
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "organizer_email": meeting.organizer_email,
                "start_time": meeting.start_time.isoformat() if meeting.start_time else "",
                "end_time": meeting.end_time.isoformat() if meeting.end_time else "",
                "duration_minutes": meeting.duration_minutes,
                "participant_count": meeting.participant_count,
                "recording_sharepoint_url": meeting.recording_sharepoint_url or "",
                "recording_url": meeting.recording_url or ""
            }

            # Add transcript SharePoint URL if available
            from ...core.database import Transcript
            transcript = session.query(Transcript).filter_by(
                meeting_id=meeting.id
            ).first()
            if transcript and transcript.transcript_sharepoint_url:
                meeting_metadata["transcript_sharepoint_url"] = transcript.transcript_sharepoint_url

            # Prepare enhanced summary data
            enhanced_summary_data = {
                "action_items": summary.action_items_json or [],
                "decisions": summary.decisions_json or [],
                "topics": summary.topics_json or [],
                "highlights": summary.highlights_json or [],
                "mentions": summary.mentions_json or []
            }

            # Build transcript stats
            transcript_stats = {
                "word_count": transcript.word_count if transcript else 0,
                "speaker_count": transcript.speaker_count if transcript else 0
            }

        # Send personalized email
        from_email = self.config.app.email_from or "noreply@townsquaremedia.com"

        try:
            message_id = self.email_sender.send_personalized_summary(
                from_email=from_email,
                to_email=user_email,
                subject=f"Your Personalized Summary: {meeting.subject}",
                summary_markdown=summary.summary_text,
                meeting_metadata=meeting_metadata,
                enhanced_summary_data=enhanced_summary_data,
                transcript_stats=transcript_stats,
                include_footer=True
            )

            logger.info(f"✓ Sent personalized email to {user_email} (message_id: {message_id})")

        except EmailSendError as e:
            raise ChatCommandError(f"Failed to send email: {e}")

        # Post confirmation to chat
        confirmation = (
            f"✅ @{user_name}: Personalized summary sent to {user_email}!\n\n"
            f"Your email includes:\n"
            f"• Times you were mentioned\n"
            f"• Your assigned action items\n"
            f"• Full meeting summary for context"
        )

        await self.chat_poster.post_message(chat_id=chat_id, content=confirmation)

        return {
            "action_taken": f"Sent personalized email to {user_email}",
            "confirmation_message": confirmation
        }

    async def _handle_email_all(
        self,
        meeting: Meeting,
        user_email: str,
        user_name: str,
        chat_id: str,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle 'email all' command - send standard email to all participants.

        Only organizers can use this command.

        Args:
            meeting: Meeting object
            user_email: User email
            user_name: User display name
            chat_id: Chat thread ID
            parameters: Command parameters

        Returns:
            Result dictionary with action_taken and confirmation_message
        """
        logger.info(f"Handling email_all command from {user_email}")

        # Verify user is organizer
        if user_email.lower() != meeting.organizer_email.lower():
            raise ChatCommandError(
                "Only the meeting organizer can use the 'email all' command."
            )

        # Queue distribution job (standard email to all)
        job = self.queue.create_job(
            job_type="distribute",
            input_data={
                "meeting_id": meeting.id,
                "triggered_by": f"chat_command:{user_email}",
                "force_email": True  # Override preference checking
            },
            priority=8  # Higher priority
        )

        logger.info(f"Queued distribution job {job.id} for meeting {meeting.id}")

        # Post confirmation to chat
        confirmation = (
            f"✅ @{user_name}: Sending meeting summary to all participants...\n\n"
            f"Emails will be sent within a few minutes."
        )

        await self.chat_poster.post_message(chat_id=chat_id, content=confirmation)

        return {
            "action_taken": f"Queued email distribution to all participants (job {job.id})",
            "confirmation_message": confirmation
        }

    async def _handle_no_emails(
        self,
        meeting: Meeting,
        user_email: str,
        user_name: str,
        chat_id: str,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle 'no emails' command - opt user out of email summaries.

        Args:
            meeting: Meeting object
            user_email: User email
            user_name: User display name
            chat_id: Chat thread ID
            parameters: Command parameters

        Returns:
            Result dictionary with action_taken and confirmation_message
        """
        logger.info(f"Handling no_emails command for {user_email}")

        # Update user preference
        success = self.pref_manager.set_user_preference(
            email=user_email,
            receive_emails=False,
            updated_by="user"
        )

        if not success:
            raise ChatCommandError("Failed to update email preferences. Please try again.")

        logger.info(f"✓ User {user_email} opted out of emails")

        # Post confirmation to chat
        confirmation = (
            f"✅ @{user_name}: You've been opted out of automatic meeting summary emails.\n\n"
            f"You can still:\n"
            f"• View summaries in this Teams chat\n"
            f"• Use '@meeting notetaker email me' to request summaries on-demand\n\n"
            f"To opt back in, contact your admin."
        )

        await self.chat_poster.post_message(chat_id=chat_id, content=confirmation)

        return {
            "action_taken": f"Opted {user_email} out of email summaries",
            "confirmation_message": confirmation
        }

    async def _handle_summarize_again(
        self,
        meeting: Meeting,
        user_email: str,
        user_name: str,
        chat_id: str,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle 'summarize again' command - regenerate summary with custom instructions.

        Args:
            meeting: Meeting object
            user_email: User email
            user_name: User display name
            chat_id: Chat thread ID
            parameters: Command parameters (contains 'instructions')

        Returns:
            Result dictionary with action_taken and confirmation_message
        """
        custom_instructions = parameters.get("instructions")

        if not custom_instructions:
            raise ChatCommandError(
                "Please provide instructions for re-summarization.\n"
                "Example: `@meeting notetaker summarize again focus on engineering tasks`"
            )

        logger.info(
            f"Handling summarize_again command for meeting {meeting.id} "
            f"with instructions: {custom_instructions}"
        )

        # Get current summary version
        with self.db.get_session() as session:
            latest_summary = session.query(Summary).filter_by(
                meeting_id=meeting.id
            ).order_by(
                Summary.version.desc()
            ).first()

            if not latest_summary:
                raise ChatCommandError(
                    "No existing summary found. Cannot re-summarize."
                )

            next_version = latest_summary.version + 1

        # Create new summary job with custom instructions
        job = self.queue.create_job(
            job_type="generate_summary",
            input_data={
                "meeting_id": meeting.id,
                "custom_instructions": custom_instructions,
                "version": next_version,
                "triggered_by": f"chat_command:{user_email}"
            },
            priority=8  # Higher priority
        )

        logger.info(
            f"Queued re-summarization job {job.id} for meeting {meeting.id} "
            f"(version {next_version})"
        )

        # Post confirmation to chat
        confirmation = (
            f"✅ @{user_name}: Generating new summary with your instructions...\n\n"
            f"**Your instructions:**\n"
            f"> {custom_instructions}\n\n"
            f"I'll post the new summary here when it's ready (usually 30-60 seconds)."
        )

        await self.chat_poster.post_message(chat_id=chat_id, content=confirmation)

        return {
            "action_taken": (
                f"Queued re-summarization (version {next_version}) "
                f"with instructions: {custom_instructions}"
            ),
            "confirmation_message": confirmation
        }
