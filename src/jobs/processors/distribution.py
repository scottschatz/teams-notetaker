"""
Distribution Processor

Distributes meeting summaries via email and Teams chat.
Third and final processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...graph.client import GraphAPIClient
from ...graph.mail import EmailSender
from ...graph.chat import TeamsChatPoster
from ...core.database import (
    Distribution, Summary, Meeting, MeetingParticipant, Transcript
)
from ...core.exceptions import EmailSendError, TeamsChatPostError, DistributionError
from ...preferences.user_preferences import PreferenceManager


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

        # Initialize preference manager (for opt-in/opt-out filtering)
        self.pref_manager = PreferenceManager(db)

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
        resend_target = job.input_data.get("resend_target")  # 'organizer', 'subscribers', 'both', or None

        self._log_progress(job, f"Distributing summary for meeting {meeting_id}" +
                          (f" (resend to: {resend_target})" if resend_target else ""))

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

            # Filter by resend_target if specified
            if resend_target:
                if resend_target == 'organizer':
                    # Only send to organizer
                    participant_emails = [meeting.organizer_email] if meeting.organizer_email else []
                    self._log_progress(job, f"Resending to organizer only: {meeting.organizer_email}")
                elif resend_target == 'subscribers':
                    # Only send to subscribers (filter out organizer)
                    participant_emails = [e for e in participant_emails if e != meeting.organizer_email]
                    self._log_progress(job, f"Resending to subscribers only (excluding organizer)")
                # 'both' or any other value: send to all (no filtering)

            # Filter by preferences using priority logic (opt-in/opt-out system)
            filtered_emails = []
            for email in participant_emails:
                # For resend_target='organizer', skip preference check (always send to organizer)
                if resend_target == 'organizer':
                    filtered_emails.append(email)
                elif self.pref_manager.should_send_email(email, meeting_id):
                    filtered_emails.append(email)
                else:
                    logger.debug(f"Skipping {email} based on preferences")

            participant_emails = filtered_emails

            self._log_progress(
                job,
                f"After filtering preferences: {len(participant_emails)} opted-in recipient(s)"
            )

            if not participant_emails:
                self._log_progress(
                    job,
                    "No opted-in participants, skipping email distribution",
                    "warning"
                )
                # Note: Still post to chat below, just don't send emails

            # Build meeting metadata with SharePoint URLs
            # Convert times to Eastern timezone for display
            import pytz
            eastern = pytz.timezone('America/New_York')

            start_time_eastern = ""
            end_time_eastern = ""
            if meeting.start_time:
                # Database stores times as naive datetimes in UTC - add UTC timezone first
                start_utc = meeting.start_time.replace(tzinfo=pytz.UTC) if meeting.start_time.tzinfo is None else meeting.start_time
                start_time_eastern = start_utc.astimezone(eastern).strftime("%a, %b %d, %Y at %I:%M %p %Z")
            if meeting.end_time:
                # Database stores times as naive datetimes in UTC - add UTC timezone first
                end_utc = meeting.end_time.replace(tzinfo=pytz.UTC) if meeting.end_time.tzinfo is None else meeting.end_time
                end_time_eastern = end_utc.astimezone(eastern).strftime("%a, %b %d, %Y at %I:%M %p %Z")

            meeting_metadata = {
                "meeting_id": meeting.id,
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "organizer_email": meeting.organizer_email,
                "start_time": start_time_eastern,
                "end_time": end_time_eastern,
                "duration_minutes": meeting.duration_minutes,
                "participant_count": meeting.participant_count,
                "join_url": meeting.join_url or "",
                "recording_url": meeting.recording_url or "",
                "recording_sharepoint_url": meeting.recording_sharepoint_url or "",
                "chat_id": meeting.chat_id or ""
            }

            # Get transcript with SharePoint URL
            transcript_obj = session.query(Transcript).filter_by(meeting_id=meeting_id).first()
            if transcript_obj and transcript_obj.transcript_sharepoint_url:
                meeting_metadata["transcript_sharepoint_url"] = transcript_obj.transcript_sharepoint_url

            # Prepare enhanced summary data
            enhanced_summary_data = {
                "action_items": summary.action_items_json or [],
                "decisions": summary.decisions_json or [],
                "topics": summary.topics_json or [],
                "highlights": summary.highlights_json or [],
                "mentions": summary.mentions_json or [],
                "key_numbers": summary.key_numbers_json or []  # NEW: Financial/quantitative metrics
            }

            # Build transcript stats (v2.1: includes speaker breakdown)
            transcript_stats = {
                "word_count": transcript_obj.word_count if transcript_obj else 0,
                "speaker_count": transcript_obj.speaker_count if transcript_obj else 0
            }

            # Extract detailed speaker stats from transcript VTT (v2.1)
            if transcript_obj and transcript_obj.vtt_content:
                try:
                    from src.utils.transcript_stats import extract_transcript_stats
                    detailed_stats = extract_transcript_stats(transcript_obj.vtt_content)
                    transcript_stats["speaker_details"] = detailed_stats.get("speakers", [])
                    transcript_stats["actual_duration_minutes"] = detailed_stats.get("actual_duration_minutes", 0)
                except Exception as e:
                    self._log_progress(job, f"Could not extract detailed speaker stats: {e}", "warning")
                    transcript_stats["speaker_details"] = []

            distribution_results = []
            email_sent = False
            chat_sent = False
            email_message_id = None
            chat_message_id = None

            # POST TO CHAT FIRST (chat-first strategy)
            if self.config.app.teams_chat_enabled and meeting.chat_id:
                try:
                    self._log_progress(job, "Posting summary to Teams meeting chat")

                    # Run in executor to avoid blocking event loop
                    loop = asyncio.get_event_loop()
                    chat_message_id = await loop.run_in_executor(
                        None,
                        lambda: self.chat_poster.post_meeting_summary(
                            chat_id=meeting.chat_id,
                            summary_markdown=summary.summary_text,
                            meeting_metadata=meeting_metadata,
                            enhanced_summary_data=enhanced_summary_data,
                            include_header=True
                        )
                    )

                    chat_sent = True

                    self._log_progress(job, f"✓ Posted to Teams chat (message_id: {chat_message_id})")

                    # Create distribution record
                    dist = Distribution(
                        meeting_id=meeting_id,
                        summary_id=summary.id,
                        distribution_type="teams_chat",
                        recipient=f"chat:{meeting.chat_id}",
                        status="sent",
                        message_id=chat_message_id,
                        sent_at=datetime.now()
                    )
                    session.add(dist)
                    distribution_results.append("teams_chat")

                except TeamsChatPostError as e:
                    self._log_progress(job, f"Teams chat posting failed: {e}", "error")
                    # Don't raise - continue with email
                    chat_sent = False

            else:
                if not self.config.app.teams_chat_enabled:
                    self._log_progress(job, "Teams chat distribution disabled in config", "info")
                elif not meeting.chat_id:
                    self._log_progress(job, "No Teams chat ID found for meeting", "warning")

            # THEN send email (if enabled)
            if self.config.app.email_enabled:
                try:
                    # DEBUG MODE: Filter recipients if debug_mode is enabled
                    if hasattr(self.config.app, 'debug_mode') and self.config.app.debug_mode:
                        if hasattr(self.config.app, 'debug_email_recipients') and self.config.app.debug_email_recipients:
                            original_count = len(participant_emails)
                            participant_emails = [
                                email for email in participant_emails
                                if email in self.config.app.debug_email_recipients
                            ]
                            self._log_progress(
                                job,
                                f"⚠️ DEBUG MODE: Filtered {original_count} recipients to {len(participant_emails)} debug recipients",
                                "warning"
                            )
                        else:
                            self._log_progress(job, "⚠️ DEBUG MODE enabled but no debug recipients configured, skipping email", "warning")
                            participant_emails = []

                    if not participant_emails:
                        self._log_progress(job, "No email recipients after filtering", "warning")
                        email_sent = False
                    else:
                        self._log_progress(job, f"Sending email to {len(participant_emails)} recipients")

                        from_email = self.config.app.email_from or "noreply@townsquaremedia.com"

                        # Format participants list for email template (deduplicated by email/name)
                        # Enrich with photos and job titles (run in executor to avoid blocking)
                        loop = asyncio.get_event_loop()
                        seen_identifiers = set()  # Track by email or display_name
                        participants_dict = []
                        for p in participants:
                            email_lower = p.email.lower() if p.email else ""
                            # Use email as identifier if available, otherwise use display_name
                            identifier = email_lower if email_lower else (p.display_name or "").lower()

                            if identifier and identifier not in seen_identifiers:
                                # Only enrich with photo/title if participant has email (internal user)
                                if email_lower:
                                    enriched = await loop.run_in_executor(
                                        None,
                                        lambda email=p.email, name=p.display_name: self.graph_client.enrich_user_with_photo_and_title(
                                            email, name
                                        )
                                    )
                                else:
                                    # PSTN/external participants - no enrichment available
                                    enriched = {}

                                participants_dict.append({
                                    "email": p.email,  # May be None for PSTN
                                    "display_name": p.display_name,
                                    "role": p.role,
                                    "job_title": enriched.get("jobTitle"),
                                    "photo_base64": enriched.get("photo_base64")
                                })
                                seen_identifiers.add(identifier)

                        # Fetch meeting invitees for "Invited" section
                        invitees_list = []
                        if meeting.join_url and meeting.organizer_user_id:
                            try:
                                # Find online meeting by join URL
                                online_meetings = await loop.run_in_executor(
                                    None,
                                    lambda: self.graph_client.get(
                                        f"/users/{meeting.organizer_user_id}/onlineMeetings",
                                        params={"$filter": f"joinWebUrl eq '{meeting.join_url}'"}
                                    )
                                )

                                if online_meetings.get("value"):
                                    om = online_meetings["value"][0]
                                    om_participants = om.get("participants", {})

                                    # Get attendee emails that actually joined (for filtering)
                                    attendee_emails = {p.email.lower() for p in participants if p.email}

                                    # Collect invitees (organizer + attendees from invite)
                                    all_invitees = []

                                    # Add organizer
                                    org = om_participants.get("organizer", {})
                                    if org.get("upn"):
                                        all_invitees.append({
                                            "email": org["upn"].lower(),
                                            "name": org.get("identity", {}).get("user", {}).get("displayName")
                                        })

                                    # Add invited attendees
                                    for att in om_participants.get("attendees", []):
                                        if att.get("upn"):
                                            all_invitees.append({
                                                "email": att["upn"].lower(),
                                                "name": att.get("identity", {}).get("user", {}).get("displayName")
                                            })

                                    # Filter out invitees who actually attended (by email)
                                    for inv in all_invitees:
                                        if inv["email"] not in attendee_emails:
                                            # Look up display name if not available
                                            if not inv["name"]:
                                                try:
                                                    user_info = await loop.run_in_executor(
                                                        None,
                                                        lambda email=inv["email"]: self.graph_client.get(f"/users/{email}")
                                                    )
                                                    inv["name"] = user_info.get("displayName", inv["email"])
                                                except:
                                                    inv["name"] = inv["email"].split("@")[0]
                                            invitees_list.append(inv)

                                    if invitees_list:
                                        self._log_progress(job, f"Found {len(invitees_list)} invitees who may not have attended")

                            except Exception as e:
                                self._log_progress(job, f"Could not fetch meeting invitees: {e}", "warning")

                        # Format meeting time in Eastern timezone for subject
                        import pytz
                        eastern = pytz.timezone('America/New_York')

                        # Convert UTC to Eastern
                        if meeting.start_time:
                            # Database stores times as naive datetimes in UTC - add UTC timezone first
                            start_utc = meeting.start_time.replace(tzinfo=pytz.UTC) if meeting.start_time.tzinfo is None else meeting.start_time
                            meeting_time_eastern = start_utc.astimezone(eastern)
                            # Format: "Mon, Dec 16 at 10:00 AM EST"
                            time_str = meeting_time_eastern.strftime("%a, %b %d at %I:%M %p %Z")
                        else:
                            time_str = "Unknown Time"

                        # Run in executor to avoid blocking event loop
                        loop = asyncio.get_event_loop()
                        email_message_id = await loop.run_in_executor(
                            None,
                            lambda: self.email_sender.send_meeting_summary(
                                from_email=from_email,
                                to_emails=participant_emails,
                                subject=f"Meeting Summary: {meeting.subject} ({time_str})",
                                summary_markdown=summary.summary_text,
                                meeting_metadata=meeting_metadata,
                                enhanced_summary_data=enhanced_summary_data,
                                transcript_content=None,  # Using SharePoint links instead
                                participants=participants_dict,
                                transcript_stats=transcript_stats,
                                invitees=invitees_list,  # NEW: Invitees who may not have attended
                                include_footer=True
                            )
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

            # Update meeting status (query it in THIS session to avoid detached object bug)
            meeting_in_session = session.query(Meeting).filter_by(id=meeting_id).first()
            if meeting_in_session:
                meeting_in_session.has_distribution = True
                meeting_in_session.status = "completed"

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
