"""
Distribution Processor

Distributes meeting summaries via email and Teams chat.
Third and final processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...graph.client import GraphAPIClient
from ...graph.mail import EmailSender
from ...graph.chat import TeamsChatPoster
from ...core.database import (
    Distribution, Summary, Meeting, MeetingParticipant
)
from ...core.exceptions import EmailSendError, TeamsChatPostError, DistributionError


logger = logging.getLogger(__name__)


@register_processor("distribute")
class DistributionProcessor(BaseProcessor):
    """
    Distributes meeting summaries via email and Teams chat.

    Input (job.input_data):
        - meeting_id: Database meeting ID

    Output (job.output_data):
        - success: bool
        - email_sent: bool
        - email_recipient_count: Number of email recipients
        - email_message_id: Email message ID
        - chat_sent: bool
        - chat_message_id: Teams chat message ID
        - distribution_count: Number of distribution records created
        - message: Status message

    Updates:
        - meetings.has_distribution = True
        - meetings.status = 'completed'
        - Creates distribution records in database

    Errors:
        - EmailSendError: Email sending failed
        - TeamsChatPostError: Teams chat posting failed
        - DistributionError: General distribution error
    """

    def __init__(self, db, config):
        """
        Initialize distribution processor.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
        """
        super().__init__(db, config)

        # Initialize Graph API client, email sender, and chat poster
        self.graph_client = GraphAPIClient(config.graph_api)
        self.email_sender = EmailSender(self.graph_client)
        self.chat_poster = TeamsChatPoster(self.graph_client)

    async def process(self, job) -> Dict[str, Any]:
        """
        Process distribute job.

        Args:
            job: JobQueue object

        Returns:
            Output data dictionary
        """
        # Validate input
        self._validate_job_input(job, required_fields=["meeting_id"])

        meeting_id = job.input_data["meeting_id"]

        self._log_progress(job, f"Distributing summary for meeting {meeting_id}")

        # Get meeting, summary, and participants from database
        meeting = self._get_meeting(meeting_id)

        with self.db.get_session() as session:
            # Get summary
            summary = session.query(Summary).filter_by(meeting_id=meeting_id).first()
            if not summary:
                raise DistributionError(f"No summary found for meeting {meeting_id}")

            # Get participants
            participants = session.query(MeetingParticipant).filter_by(
                meeting_id=meeting_id
            ).all()

            if not participants:
                self._log_progress(job, "No participants found, skipping distribution", "warning")
                return self._create_output_data(
                    success=True,
                    message="No participants to distribute to",
                    email_sent=False,
                    chat_sent=False,
                    distribution_count=0
                )

            participant_emails = [p.email for p in participants if p.email]

            self._log_progress(
                job,
                f"Found {len(participant_emails)} participant email(s)"
            )

            # Build meeting metadata
            meeting_metadata = {
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "start_time": meeting.start_time.isoformat() if meeting.start_time else "",
                "end_time": meeting.end_time.isoformat() if meeting.end_time else "",
                "duration_minutes": meeting.duration_minutes,
                "participant_count": meeting.participant_count
            }

            distribution_results = []
            email_sent = False
            chat_sent = False
            email_message_id = None
            chat_message_id = None

            # Send email (if enabled)
            if self.config.app.email_enabled:
                try:
                    self._log_progress(job, f"Sending email to {len(participant_emails)} recipients")

                    from_email = self.config.app.email_from or "noreply@townsquaremedia.com"

                    email_message_id = self.email_sender.send_meeting_summary(
                        from_email=from_email,
                        to_emails=participant_emails,
                        subject=f"Meeting Summary: {meeting.subject}",
                        summary_markdown=summary.summary_text,
                        meeting_metadata=meeting_metadata,
                        include_footer=True
                    )

                    email_sent = True

                    self._log_progress(job, f"✓ Email sent successfully (message_id: {email_message_id})")

                    # Create distribution records for each recipient
                    for recipient_email in participant_emails:
                        dist = Distribution(
                            meeting_id=meeting_id,
                            summary_id=summary.id,
                            distribution_type="email",
                            recipient=recipient_email,
                            status="sent",
                            message_id=email_message_id,
                            sent_at=datetime.now()
                        )
                        session.add(dist)
                        distribution_results.append("email")

                except EmailSendError as e:
                    self._log_progress(job, f"Email sending failed: {e}", "error")

                    # Create failed distribution records
                    for recipient_email in participant_emails:
                        dist = Distribution(
                            meeting_id=meeting_id,
                            summary_id=summary.id,
                            distribution_type="email",
                            recipient=recipient_email,
                            status="failed",
                            error_message=str(e)
                        )
                        session.add(dist)

                    # Don't raise - continue with Teams chat
                    email_sent = False

            else:
                self._log_progress(job, "Email distribution disabled in config", "info")

            # Post to Teams chat (if enabled)
            if self.config.app.teams_chat_enabled:
                try:
                    self._log_progress(job, "Posting summary to Teams meeting chat")

                    # Get meeting chat ID
                    # This requires the meeting to have chat info
                    # For now, attempt to get it from Graph API
                    chat_id = self.chat_poster.get_meeting_chat_id(
                        meeting_id=meeting.meeting_id,  # Graph API meeting ID
                        user_id=meeting.organizer_email
                    )

                    if chat_id:
                        chat_message_id = self.chat_poster.post_meeting_summary(
                            chat_id=chat_id,
                            summary_markdown=summary.summary_text,
                            meeting_metadata=meeting_metadata,
                            include_header=True
                        )

                        chat_sent = True

                        self._log_progress(job, f"✓ Posted to Teams chat (message_id: {chat_message_id})")

                        # Create distribution record
                        dist = Distribution(
                            meeting_id=meeting_id,
                            summary_id=summary.id,
                            distribution_type="teams_chat",
                            recipient=f"chat:{chat_id}",
                            status="sent",
                            message_id=chat_message_id,
                            sent_at=datetime.now()
                        )
                        session.add(dist)
                        distribution_results.append("teams_chat")

                    else:
                        self._log_progress(job, "No Teams chat found for meeting", "warning")

                except TeamsChatPostError as e:
                    self._log_progress(job, f"Teams chat posting failed: {e}", "error")

                    # Create failed distribution record
                    dist = Distribution(
                        meeting_id=meeting_id,
                        summary_id=summary.id,
                        distribution_type="teams_chat",
                        recipient=f"meeting:{meeting.meeting_id}",
                        status="failed",
                        error_message=str(e)
                    )
                    session.add(dist)

                    # Don't raise - distribution partially succeeded
                    chat_sent = False

            else:
                self._log_progress(job, "Teams chat distribution disabled in config", "info")

            # Update meeting status
            meeting.has_distribution = True
            meeting.status = "completed"

            session.commit()

            distribution_count = len(distribution_results)

            # Determine overall success
            success = email_sent or chat_sent

            if success:
                message = f"Distribution completed: "
                if email_sent:
                    message += f"email to {len(participant_emails)} recipients"
                if chat_sent:
                    if email_sent:
                        message += ", "
                    message += "Teams chat posted"
            else:
                message = "Distribution failed for all channels"

            self._log_progress(job, f"✓ {message}")

            return self._create_output_data(
                success=success,
                message=message,
                email_sent=email_sent,
                email_recipient_count=len(participant_emails) if email_sent else 0,
                email_message_id=email_message_id,
                chat_sent=chat_sent,
                chat_message_id=chat_message_id,
                distribution_count=distribution_count
            )
