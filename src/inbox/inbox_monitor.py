"""
Inbox Monitor

Periodically checks inbox for commands and processes them:
- Subscribe/unsubscribe updates user preferences
- Feedback is stored in database
- Auto-replies are ignored
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from .email_parser import EmailCommandParser, EmailCommandType, ParsedEmailCommand
from .inbox_reader import InboxReader
from ..graph.client import GraphAPIClient
from ..core.database import DatabaseManager, ProcessedInboxMessage, UserFeedback, EmailAlias
from ..preferences.user_preferences import PreferenceManager
from ..core.exceptions import GraphAPIError


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

    # Cache TTL for email aliases (7 days) - after this we re-fetch from Graph API
    ALIAS_CACHE_TTL_DAYS = 7

    def _is_cache_expired(self, cached: EmailAlias) -> bool:
        """Check if a cached email alias record has expired."""
        if not cached.resolved_at:
            return True
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age = now - cached.resolved_at
        return age > timedelta(days=self.ALIAS_CACHE_TTL_DAYS)

    def _resolve_primary_email(self, sender_email: str, sender_name: str = "") -> str:
        """
        Resolve an email alias to the user's primary email address.

        Users may send from aliases (e.g., scott.s@company.com) but their
        primary email (sschatz@company.com) is what appears in meeting
        participant lists. We need to use the primary email for preference
        matching.

        The mapping is cached in the database to avoid repeated Graph API calls.
        Cache expires after ALIAS_CACHE_TTL_DAYS days and is refreshed from Graph API.

        Args:
            sender_email: The email address from the incoming message
            sender_name: Optional display name from the email

        Returns:
            The user's primary email address, or original if lookup fails
        """
        alias_lower = sender_email.lower().strip()

        # Check database cache first
        with self.db.get_session() as session:
            cached = session.query(EmailAlias).filter_by(alias_email=alias_lower).first()
            if cached and not self._is_cache_expired(cached):
                # Update last_used_at timestamp (naive UTC for database)
                cached.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
                session.commit()
                logger.debug(f"Email alias cache hit: {alias_lower} -> {cached.primary_email}")
                return cached.primary_email
            elif cached:
                logger.debug(f"Email alias cache expired for {alias_lower}, refreshing from Graph API")

        # Not in cache or expired - look up via Graph API
        try:
            user_info = self.graph_client.get(
                f"/users/{alias_lower}",
                params={"$select": "id,mail,userPrincipalName,displayName,jobTitle"}
            )

            # Get user ID (stable GUID that never changes)
            user_id = user_info.get("id")

            # Get primary email (prefer 'mail' field, fall back to UPN)
            primary_email = user_info.get("mail") or user_info.get("userPrincipalName", "")
            primary_email = primary_email.lower().strip()
            display_name = user_info.get("displayName", sender_name)
            job_title = user_info.get("jobTitle", "")

            # Cache the result in database (set resolved_at for cache expiration tracking)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.get_session() as session:
                # Store alias -> primary mapping with user ID
                alias_record = EmailAlias(
                    alias_email=alias_lower,
                    primary_email=primary_email or alias_lower,
                    user_id=user_id,
                    display_name=display_name,
                    job_title=job_title,
                    resolved_at=now,
                    last_used_at=now
                )
                session.merge(alias_record)  # Use merge to handle duplicates

                # If this was an alias, also store primary -> primary
                if primary_email and primary_email != alias_lower:
                    primary_record = EmailAlias(
                        alias_email=primary_email,
                        primary_email=primary_email,
                        user_id=user_id,
                        display_name=display_name,
                        job_title=job_title,
                        resolved_at=now,
                        last_used_at=now
                    )
                    session.merge(primary_record)

                session.commit()

            if primary_email and primary_email != alias_lower:
                logger.info(f"Resolved email alias: {alias_lower} -> {primary_email} (user_id: {user_id})")
                return primary_email
            else:
                return alias_lower

        except GraphAPIError as e:
            # User not found or API error - cache the original email with expiration
            # so we retry later (user might become resolvable)
            logger.warning(f"Could not resolve email {alias_lower}: {e}")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.get_session() as session:
                alias_record = EmailAlias(
                    alias_email=alias_lower,
                    primary_email=alias_lower,  # Same as alias since we can't resolve
                    display_name=sender_name,
                    resolved_at=now,  # Set so cache can expire and retry
                    last_used_at=now
                )
                session.merge(alias_record)
                session.commit()
            return alias_lower
        except Exception as e:
            logger.error(f"Unexpected error resolving email {alias_lower}: {e}")
            return alias_lower

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
            # Calculate lookback time (naive UTC for Graph API)
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=self.lookback_minutes)

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
            # Resolve email alias to primary email (for matching with meeting participants)
            primary_email = self._resolve_primary_email(parsed.sender_email, parsed.sender_name)

            # Update user preference using primary email
            self.pref_manager.set_user_preference(
                email=primary_email,
                receive_emails=True,
                updated_by="inbox_command"
            )

            if primary_email != parsed.sender_email:
                logger.info(f"User subscribed via email: {parsed.sender_email} (resolved to {primary_email})")
            else:
                logger.info(f"User subscribed via email: {parsed.sender_email}")

            # Send confirmation to original sender address
            self._send_subscribe_confirmation(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing with primary email
            self._mark_processed(message_id, "subscribe", primary_email, f"Successfully subscribed (from: {parsed.sender_email})")

            return "subscribed"

        except Exception as e:
            logger.error(f"Failed to process subscribe from {parsed.sender_email}: {e}")
            self._mark_processed(message_id, "subscribe", parsed.sender_email, f"Error: {e}")
            return "error"

    async def _handle_unsubscribe(self, msg: Dict[str, Any], parsed: ParsedEmailCommand) -> str:
        """Handle unsubscribe command."""
        message_id = msg.get("internet_message_id") or msg.get("id")

        try:
            # Resolve email alias to primary email (for matching with meeting participants)
            primary_email = self._resolve_primary_email(parsed.sender_email, parsed.sender_name)

            # Update user preference using primary email
            self.pref_manager.set_user_preference(
                email=primary_email,
                receive_emails=False,
                updated_by="inbox_command"
            )

            if primary_email != parsed.sender_email:
                logger.info(f"User unsubscribed via email: {parsed.sender_email} (resolved to {primary_email})")
            else:
                logger.info(f"User unsubscribed via email: {parsed.sender_email}")

            # Send confirmation to original sender address
            self._send_unsubscribe_confirmation(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing with primary email
            self._mark_processed(message_id, "unsubscribe", primary_email, f"Successfully unsubscribed (from: {parsed.sender_email})")

            return "unsubscribed"

        except Exception as e:
            logger.error(f"Failed to process unsubscribe from {parsed.sender_email}: {e}")
            self._mark_processed(message_id, "unsubscribe", parsed.sender_email, f"Error: {e}")
            return "error"

    async def _handle_feedback(self, msg: Dict[str, Any], parsed: ParsedEmailCommand) -> str:
        """Handle feedback email."""
        message_id = msg.get("internet_message_id") or msg.get("id")

        try:
            # Resolve email alias to primary email
            primary_email = self._resolve_primary_email(parsed.sender_email, parsed.sender_name)

            # Store feedback in database using primary email
            with self.db.get_session() as session:
                feedback = UserFeedback(
                    user_email=primary_email,
                    feedback_text=parsed.body or parsed.subject,
                    subject=parsed.subject,
                    source_email_id=message_id,
                )
                session.add(feedback)
                session.commit()

            logger.info(f"Stored feedback from {parsed.sender_email}: {parsed.subject[:50]}")

            # Send acknowledgment to original sender address
            self._send_feedback_acknowledgment(parsed.sender_email, parsed.sender_name)

            # Mark as read
            self.inbox_reader.mark_as_read(msg.get("id"))

            # Record processing with primary email
            self._mark_processed(message_id, "feedback", primary_email, "Feedback stored")

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
            <p>If you ever want to unsubscribe, click the button below or reply to any summary email with "unsubscribe" in the subject line.</p>
            <p style="margin: 20px 0;">
                <a href="mailto:{self.mailbox_email}?subject=unsubscribe"
                   style="display: inline-block; padding: 12px 24px; background-color: #6b7280; color: white;
                          text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Unsubscribe
                </a>
            </p>
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
            <p>If you change your mind, you can subscribe again by clicking the button below or sending an email with "subscribe" in the subject line.</p>
            <p style="margin: 20px 0;">
                <a href="mailto:{self.mailbox_email}?subject=subscribe"
                   style="display: inline-block; padding: 12px 24px; background-color: #2563eb; color: white;
                          text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Resubscribe
                </a>
            </p>
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
