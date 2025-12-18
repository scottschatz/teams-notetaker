"""
Inbox Monitor

Periodically checks inbox for commands and processes them:
- Subscribe/unsubscribe updates user preferences
- Feedback is stored in database
- Auto-replies are ignored
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from .email_parser import EmailCommandParser, EmailCommandType, ParsedEmailCommand
from .inbox_reader import InboxReader
from ..graph.client import GraphAPIClient
from ..core.database import DatabaseManager, ProcessedInboxMessage, UserFeedback
from ..preferences.user_preferences import PreferenceManager


logger = logging.getLogger(__name__)


class InboxMonitor:
    """
    Monitors inbox and processes user commands.

    This replaces the broken chat monitoring functionality with
    a more reliable email-based approach.
    """

    def __init__(
        self,
        db: DatabaseManager,
        graph_client: GraphAPIClient,
        mailbox_email: str,
        lookback_minutes: int = 60
    ):
        """
        Initialize inbox monitor.

        Args:
            db: Database manager instance
            graph_client: Authenticated Graph API client
            mailbox_email: Email address of the shared mailbox to monitor
            lookback_minutes: How far back to look for messages (default: 60 min)
        """
        self.db = db
        self.graph_client = graph_client
        self.mailbox_email = mailbox_email
        self.lookback_minutes = lookback_minutes

        self.inbox_reader = InboxReader(graph_client, mailbox_email)
        self.parser = EmailCommandParser()
        self.pref_manager = PreferenceManager(db)

    async def check_inbox(self) -> Dict[str, Any]:
        """
        Check inbox for new commands and process them.

        Returns:
            Dictionary with processing stats:
                - checked: Total messages checked
                - processed: Messages successfully processed
                - subscribed: Users subscribed
                - unsubscribed: Users unsubscribed
                - feedback: Feedback items stored
                - skipped: Messages skipped (auto-reply, already processed)
                - errors: Messages that failed to process
        """
        stats = {
            "checked": 0,
            "processed": 0,
            "subscribed": 0,
            "unsubscribed": 0,
            "feedback": 0,
            "skipped": 0,
            "errors": 0
        }

        try:
            # Calculate lookback time
            since = datetime.utcnow() - timedelta(minutes=self.lookback_minutes)

            # Get recent messages
            messages = self.inbox_reader.get_recent_messages(since)
            stats["checked"] = len(messages)

            if not messages:
                logger.debug("No new messages in inbox")
                return stats

            logger.info(f"Checking {len(messages)} inbox messages")

            # Process each message
            for msg in messages:
                try:
                    result = await self._process_message(msg)
                    if result == "processed":
                        stats["processed"] += 1
                    elif result == "subscribed":
                        stats["subscribed"] += 1
                        stats["processed"] += 1
                    elif result == "unsubscribed":
                        stats["unsubscribed"] += 1
                        stats["processed"] += 1
                    elif result == "feedback":
                        stats["feedback"] += 1
                        stats["processed"] += 1
                    elif result == "skipped":
                        stats["skipped"] += 1
                    else:
                        stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Error processing message {msg.get('id', 'unknown')}: {e}")
                    stats["errors"] += 1

            logger.info(
                f"Inbox check complete: {stats['processed']} processed, "
                f"{stats['skipped']} skipped, {stats['errors']} errors"
            )

        except Exception as e:
            logger.error(f"Inbox check failed: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    async def _process_message(self, msg: Dict[str, Any]) -> str:
        """
        Process a single inbox message.

        Args:
            msg: Message dict from InboxReader

        Returns:
            Result string: "subscribed", "unsubscribed", "feedback", "skipped", "error"
        """
        message_id = msg.get("internet_message_id") or msg.get("id")

        # Check if already processed
        if self._is_already_processed(message_id):
            logger.debug(f"Message already processed: {message_id[:30]}...")
            return "skipped"

        # Check for auto-reply
        if self.parser.is_auto_reply(msg.get("subject", ""), msg.get("body_content", "")):
            logger.debug(f"Skipping auto-reply: {msg.get('subject', '')[:50]}")
            self._mark_processed(message_id, "auto_reply", msg.get("from_email", ""), "Skipped - auto-reply")
            return "skipped"

        # Parse the command
        parsed = self.parser.parse_email(
            sender_email=msg.get("from_email", ""),
            sender_name=msg.get("from_name", ""),
            subject=msg.get("subject", ""),
            body=msg.get("body_content", "")
        )

        # Process based on command type
        if parsed.command_type == EmailCommandType.SUBSCRIBE:
            return await self._handle_subscribe(msg, parsed)
        elif parsed.command_type == EmailCommandType.UNSUBSCRIBE:
            return await self._handle_unsubscribe(msg, parsed)
        elif parsed.command_type == EmailCommandType.FEEDBACK:
            return await self._handle_feedback(msg, parsed)
        else:
            # Unknown command - still mark as processed but don't act on it
            logger.info(f"Unknown command from {parsed.sender_email}: {msg.get('subject', '')[:50]}")
            self._mark_processed(message_id, "unknown", parsed.sender_email, "Unknown command type")
            return "skipped"

    async def _handle_subscribe(self, msg: Dict[str, Any], parsed: ParsedEmailCommand) -> str:
        """Handle subscribe command."""
        message_id = msg.get("internet_message_id") or msg.get("id")

        try:
            # Update user preference
            self.pref_manager.set_email_preference(
                user_email=parsed.sender_email,
                wants_email=True,
                source="inbox_command"
            )

            logger.info(f"User subscribed via email: {parsed.sender_email}")

            # Send confirmation
            self._send_subscribe_confirmation(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing
            self._mark_processed(message_id, "subscribe", parsed.sender_email, "Successfully subscribed")

            return "subscribed"

        except Exception as e:
            logger.error(f"Failed to process subscribe from {parsed.sender_email}: {e}")
            self._mark_processed(message_id, "subscribe", parsed.sender_email, f"Error: {e}")
            return "error"

    async def _handle_unsubscribe(self, msg: Dict[str, Any], parsed: ParsedEmailCommand) -> str:
        """Handle unsubscribe command."""
        message_id = msg.get("internet_message_id") or msg.get("id")

        try:
            # Update user preference
            self.pref_manager.set_email_preference(
                user_email=parsed.sender_email,
                wants_email=False,
                source="inbox_command"
            )

            logger.info(f"User unsubscribed via email: {parsed.sender_email}")

            # Send confirmation
            self._send_unsubscribe_confirmation(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing
            self._mark_processed(message_id, "unsubscribe", parsed.sender_email, "Successfully unsubscribed")

            return "unsubscribed"

        except Exception as e:
            logger.error(f"Failed to process unsubscribe from {parsed.sender_email}: {e}")
            self._mark_processed(message_id, "unsubscribe", parsed.sender_email, f"Error: {e}")
            return "error"

    async def _handle_feedback(self, msg: Dict[str, Any], parsed: ParsedEmailCommand) -> str:
        """Handle feedback email."""
        message_id = msg.get("internet_message_id") or msg.get("id")

        try:
            # Store feedback in database
            with self.db.get_session() as session:
                feedback = UserFeedback(
                    user_email=parsed.sender_email,
                    feedback_text=parsed.body or parsed.subject,
                    subject=parsed.subject,
                    source_email_id=message_id,
                )
                session.add(feedback)
                session.commit()

            logger.info(f"Stored feedback from {parsed.sender_email}: {parsed.subject[:50]}")

            # Send acknowledgment
            self._send_feedback_acknowledgment(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing
            self._mark_processed(message_id, "feedback", parsed.sender_email, "Feedback stored")

            return "feedback"

        except Exception as e:
            logger.error(f"Failed to store feedback from {parsed.sender_email}: {e}")
            self._mark_processed(message_id, "feedback", parsed.sender_email, f"Error: {e}")
            return "error"

    def _is_already_processed(self, message_id: str) -> bool:
        """Check if message was already processed."""
        with self.db.get_session() as session:
            existing = session.query(ProcessedInboxMessage).filter_by(
                message_id=message_id
            ).first()
            return existing is not None

    def _mark_processed(self, message_id: str, message_type: str, user_email: str, result: str):
        """Record that a message was processed."""
        try:
            with self.db.get_session() as session:
                record = ProcessedInboxMessage(
                    message_id=message_id,
                    message_type=message_type,
                    user_email=user_email,
                    result=result
                )
                session.add(record)
                session.commit()
        except Exception as e:
            logger.warning(f"Failed to record processed message: {e}")

    def _send_subscribe_confirmation(self, email: str, name: str):
        """Send subscription confirmation email."""
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2>Subscription Confirmed</h2>
            <p>Hi {name or 'there'},</p>
            <p>You've been successfully subscribed to meeting summary emails.</p>
            <p>You'll receive summaries for any Teams meetings you attend that have transcription enabled.</p>
            <p>If you ever want to unsubscribe, simply reply to any summary email with "unsubscribe" in the subject line.</p>
            <p>Best regards,<br/>Meeting Notes Bot</p>
        </body>
        </html>
        """

        self.inbox_reader.send_acknowledgment(
            to_email=email,
            to_name=name,
            subject="Meeting Summaries - Subscription Confirmed",
            body_html=body
        )

    def _send_unsubscribe_confirmation(self, email: str, name: str):
        """Send unsubscription confirmation email."""
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2>Unsubscription Confirmed</h2>
            <p>Hi {name or 'there'},</p>
            <p>You've been successfully unsubscribed from meeting summary emails.</p>
            <p>You will no longer receive automated meeting summaries.</p>
            <p>If you change your mind, you can subscribe again by sending an email with "subscribe" in the subject line.</p>
            <p>Best regards,<br/>Meeting Notes Bot</p>
        </body>
        </html>
        """

        self.inbox_reader.send_acknowledgment(
            to_email=email,
            to_name=name,
            subject="Meeting Summaries - Unsubscription Confirmed",
            body_html=body
        )

    def _send_feedback_acknowledgment(self, email: str, name: str):
        """Send feedback acknowledgment email."""
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2>Thank You for Your Feedback</h2>
            <p>Hi {name or 'there'},</p>
            <p>Thank you for taking the time to share your feedback about the meeting summary service.</p>
            <p>Your input helps us improve the service for everyone.</p>
            <p>Best regards,<br/>Meeting Notes Bot</p>
        </body>
        </html>
        """

        self.inbox_reader.send_acknowledgment(
            to_email=email,
            to_name=name,
            subject="Thank You for Your Feedback",
            body_html=body
        )
