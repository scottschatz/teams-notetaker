"""
Automatic Microsoft Graph Subscription Manager.

Ensures webhook subscriptions are always active by:
1. Checking on startup and creating if missing
2. Periodically checking and renewing/recreating as needed
3. Proactively recreating daily at a scheduled time
4. Sending email alerts when issues persist
"""

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from ..graph.client import GraphAPIClient
from ..core.config import AppConfig
from ..core.database import get_db_manager, SubscriptionEvent

logger = logging.getLogger(__name__)

# State file to persist subscription down status across restarts
SUBSCRIPTION_STATE_FILE = Path.home() / ".teams-notetaker-subscription-state"

# Max expiration for callRecords subscriptions is 4230 minutes (~2.9 days)
# We use 4200 to have a small buffer
CALLRECORDS_MAX_EXPIRATION_MINUTES = 4200

# Renew when less than this many hours remaining
RENEW_THRESHOLD_HOURS = 12

# How often to check subscription status (minutes)
CHECK_INTERVAL_MINUTES = 5

# Hour of day (UTC) to proactively recreate subscription (3 AM UTC = ~10 PM Eastern)
DAILY_RECREATE_HOUR_UTC = 3

# Startup delay to let Azure Relay connect before creating subscription
STARTUP_DELAY_SECONDS = 5

# Retry settings for failed subscription creation
MAX_CREATION_RETRIES = 5
RETRY_DELAY_SECONDS = 30


class SubscriptionManager:
    """
    Manages Microsoft Graph webhook subscriptions automatically.

    Ensures subscriptions are always active by checking periodically
    and recreating as needed. Sends email alerts when issues persist.
    """

    def __init__(self, config: AppConfig, graph_client: Optional[GraphAPIClient] = None):
        """
        Initialize subscription manager.

        Args:
            config: AppConfig instance
            graph_client: Optional GraphAPIClient (created if not provided)
        """
        self.config = config
        self.graph_client = graph_client or GraphAPIClient(config.graph_api)
        self.webhook_url = config.azure_relay.webhook_url
        self.running = False
        self._check_task: Optional[asyncio.Task] = None

        # Alert settings
        self.alert_enabled = getattr(config.app, 'alert_email_enabled', True)
        self.alert_recipients = getattr(config.app, 'alert_email_recipients', None) or []
        self.from_email = getattr(config.app, 'email_from', None)
        self._last_alert_time: Optional[datetime] = None
        self._alert_cooldown_hours = 6  # Don't spam alerts
        self._subscription_down = self._load_down_state()  # Load persisted state
        self._down_event_id: Optional[int] = None  # Track current down event for recovery logging
        self._down_timestamp: Optional[datetime] = None  # Track when we went down

    # =========================================================================
    # EVENT LOGGING
    # =========================================================================

    def _log_event(
        self,
        event_type: str,
        source: str,
        subscription_id: Optional[str] = None,
        error_message: Optional[str] = None,
        down_event_id: Optional[int] = None,
        downtime_seconds: Optional[int] = None
    ) -> Optional[int]:
        """
        Log a subscription event to the database.

        Args:
            event_type: 'down', 'up', 'created', 'renewed', 'failed'
            source: 'startup', 'check', 'daily_refresh', 'manual'
            subscription_id: Graph subscription ID if applicable
            error_message: Error details for 'down' or 'failed' events
            down_event_id: ID of corresponding 'down' event for 'up' events
            downtime_seconds: Calculated downtime for 'up' events

        Returns:
            Event ID if logged successfully, None otherwise
        """
        session = None
        try:
            db_manager = get_db_manager()
            session = db_manager.get_session()
            event = SubscriptionEvent(
                event_type=event_type,
                timestamp=datetime.utcnow(),
                subscription_id=subscription_id,
                error_message=error_message,
                down_event_id=down_event_id,
                downtime_seconds=downtime_seconds,
                source=source
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            logger.debug(f"Logged subscription event: {event_type} (source={source})")
            return event.id
        except Exception as e:
            logger.error(f"Failed to log subscription event: {e}")
            if session:
                session.rollback()
            return None
        finally:
            if session:
                session.close()

    def _log_down_event(self, source: str, error_message: str) -> Optional[int]:
        """Log that the subscription went down and store the event for recovery tracking."""
        event_id = self._log_event(
            event_type='down',
            source=source,
            error_message=error_message
        )
        if event_id:
            self._down_event_id = event_id
            self._down_timestamp = datetime.utcnow()
        return event_id

    def _log_up_event(self, source: str, subscription_id: Optional[str] = None) -> tuple:
        """
        Log that the subscription recovered and calculate downtime.

        Returns:
            Tuple of (down_timestamp, up_timestamp, downtime_seconds)
        """
        now = datetime.utcnow()
        down_timestamp = self._down_timestamp
        downtime_seconds = None

        if down_timestamp:
            downtime_seconds = int((now - down_timestamp).total_seconds())

        self._log_event(
            event_type='up',
            source=source,
            subscription_id=subscription_id,
            down_event_id=self._down_event_id,
            downtime_seconds=downtime_seconds
        )

        # Clear the down tracking
        old_down_event_id = self._down_event_id
        old_down_timestamp = self._down_timestamp
        self._down_event_id = None
        self._down_timestamp = None

        return (old_down_timestamp, now, downtime_seconds)

    def _log_created_event(self, source: str, subscription_id: str):
        """Log that a subscription was created."""
        self._log_event(
            event_type='created',
            source=source,
            subscription_id=subscription_id
        )

    def _log_renewed_event(self, source: str, subscription_id: str):
        """Log that a subscription was renewed."""
        self._log_event(
            event_type='renewed',
            source=source,
            subscription_id=subscription_id
        )

    def _log_failed_event(self, source: str, error_message: str):
        """Log that a subscription operation failed."""
        self._log_event(
            event_type='failed',
            source=source,
            error_message=error_message
        )

    def _load_down_state(self) -> bool:
        """Load subscription down state from file."""
        try:
            if SUBSCRIPTION_STATE_FILE.exists():
                content = SUBSCRIPTION_STATE_FILE.read_text().strip()
                return content == "down"
        except Exception as e:
            logger.warning(f"Failed to load subscription state: {e}")
        return False

    def _save_down_state(self, is_down: bool):
        """Save subscription down state to file."""
        try:
            if is_down:
                SUBSCRIPTION_STATE_FILE.write_text("down")
                logger.debug("Saved subscription state: down")
            else:
                # Remove file when recovered
                if SUBSCRIPTION_STATE_FILE.exists():
                    SUBSCRIPTION_STATE_FILE.unlink()
                    logger.debug("Cleared subscription down state")
        except Exception as e:
            logger.warning(f"Failed to save subscription state: {e}")

    def get_callrecords_subscriptions(self) -> list:
        """
        Get all active callRecords subscriptions.

        Returns:
            List of subscription dictionaries
        """
        try:
            response = self.graph_client.get("/subscriptions")
            all_subs = response.get("value", [])

            # Filter to only callRecords subscriptions for our webhook URL
            callrecords_subs = [
                sub for sub in all_subs
                if sub.get("resource") == "/communications/callRecords"
                and sub.get("notificationUrl") == self.webhook_url
            ]

            return callrecords_subs

        except Exception as e:
            logger.error(f"Failed to get subscriptions: {e}")
            return []

    async def create_subscription(self, source: str = 'check') -> Optional[Dict[str, Any]]:
        """
        Create a new callRecords subscription.

        Args:
            source: What triggered this creation ('startup', 'check', 'daily_refresh', 'manual')

        Returns:
            Created subscription dict or None on failure
        """
        try:
            expiry = datetime.utcnow() + timedelta(minutes=CALLRECORDS_MAX_EXPIRATION_MINUTES)

            subscription = {
                "changeType": "created",
                "notificationUrl": self.webhook_url,
                "resource": "/communications/callRecords",
                "expirationDateTime": expiry.isoformat() + "Z",
                "clientState": "teams-notetaker-secret"
            }

            logger.info(f"Creating callRecords subscription (expires: {expiry})")
            # CRITICAL: Run in executor to avoid blocking the event loop!
            # The Azure Relay listener needs the event loop to respond to validation requests.
            # If we block here, Microsoft's validation times out.
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,  # Use default thread pool
                lambda: self.graph_client.post("/subscriptions", json=subscription)
            )

            logger.info(f"✅ Subscription created: {response['id']} (expires: {response['expirationDateTime']})")
            self._log_created_event(source, response['id'])
            self._check_and_send_recovery_alert(source, response.get('id'))
            return response

        except Exception as e:
            logger.error(f"Failed to create subscription: {e}")
            self._log_failed_event(source, str(e))
            return None

    def delete_subscription(self, subscription_id: str) -> bool:
        """
        Delete a subscription.

        Args:
            subscription_id: Subscription ID to delete

        Returns:
            True if deleted successfully
        """
        try:
            self.graph_client.delete(f"/subscriptions/{subscription_id}")
            logger.info(f"Deleted subscription: {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete subscription {subscription_id}: {e}")
            return False

    def renew_subscription(self, subscription_id: str, source: str = 'check') -> Optional[Dict[str, Any]]:
        """
        Renew an existing subscription.

        Args:
            subscription_id: Subscription ID to renew
            source: What triggered this renewal ('startup', 'check', 'daily_refresh', 'manual')

        Returns:
            Updated subscription dict or None on failure
        """
        try:
            new_expiry = datetime.utcnow() + timedelta(minutes=CALLRECORDS_MAX_EXPIRATION_MINUTES)

            update_payload = {
                "expirationDateTime": new_expiry.isoformat() + "Z"
            }

            logger.info(f"Renewing subscription {subscription_id[:20]}... (new expiry: {new_expiry})")
            response = self.graph_client.patch(f"/subscriptions/{subscription_id}", json=update_payload)

            logger.info(f"✅ Subscription renewed: {response['expirationDateTime']}")
            self._log_renewed_event(source, subscription_id)
            self._check_and_send_recovery_alert(source, subscription_id)
            return response

        except Exception as e:
            logger.error(f"Failed to renew subscription {subscription_id}: {e}")
            self._log_failed_event(source, str(e))
            return None

    async def ensure_subscription(self, source: str = 'check') -> bool:
        """
        Ensure at least one valid callRecords subscription exists.

        Checks for existing subscriptions and creates one if:
        - No subscriptions exist
        - All existing subscriptions are expired or expiring soon

        Args:
            source: What triggered this check ('startup', 'check', 'daily_refresh', 'manual')

        Returns:
            True if a valid subscription exists (or was created)
        """
        logger.info("Checking callRecords subscription status...")

        subscriptions = self.get_callrecords_subscriptions()

        if not subscriptions:
            logger.warning("No callRecords subscriptions found, creating one...")
            return await self.create_subscription(source) is not None

        # Check if any subscription is still valid (not expiring soon)
        now = datetime.utcnow()
        threshold = now + timedelta(hours=RENEW_THRESHOLD_HOURS)

        for sub in subscriptions:
            expiry_str = sub.get("expirationDateTime", "")
            try:
                expiry = datetime.fromisoformat(expiry_str.replace("Z", ""))

                if expiry > threshold:
                    hours_remaining = (expiry - now).total_seconds() / 3600
                    logger.info(f"✅ Valid subscription found: {sub['id'][:20]}... ({hours_remaining:.1f}h remaining)")
                    self._check_and_send_recovery_alert(source, sub['id'])
                    return True
                else:
                    # Subscription expiring soon, try to renew
                    hours_remaining = (expiry - now).total_seconds() / 3600
                    logger.warning(f"Subscription expiring soon ({hours_remaining:.1f}h), renewing...")

                    if self.renew_subscription(sub["id"], source):
                        return True
                    else:
                        # Renewal failed, try to delete and create new
                        logger.warning("Renewal failed, recreating subscription...")
                        self.delete_subscription(sub["id"])
                        return await self.create_subscription(source) is not None

            except Exception as e:
                logger.error(f"Error parsing subscription expiry: {e}")
                continue

        # All subscriptions are invalid, create new one
        logger.warning("No valid subscriptions found, creating new one...")
        return await self.create_subscription(source) is not None

    async def recreate_subscription(self, source: str = 'daily_refresh') -> bool:
        """
        Delete all existing subscriptions and create a fresh one.

        Used for daily proactive recreation to ensure clean state.

        Args:
            source: What triggered this recreation ('startup', 'check', 'daily_refresh', 'manual')

        Returns:
            True if new subscription was created successfully
        """
        logger.info("Proactively recreating callRecords subscription...")

        # Delete all existing subscriptions
        subscriptions = self.get_callrecords_subscriptions()
        for sub in subscriptions:
            self.delete_subscription(sub["id"])

        # Create fresh subscription
        return await self.create_subscription(source) is not None

    def _check_and_send_recovery_alert(self, source: str = 'check', subscription_id: Optional[str] = None):
        """Send recovery alert if we were previously in a down state."""
        if self._subscription_down:
            # Log the up event and get timing info
            down_timestamp, up_timestamp, downtime_seconds = self._log_up_event(source, subscription_id)

            self._subscription_down = False
            self._save_down_state(False)  # Clear persisted state
            self._send_recovery_alert(down_timestamp, up_timestamp, downtime_seconds)

    def _format_downtime(self, seconds: Optional[int]) -> str:
        """Format downtime seconds into a human-readable string."""
        if seconds is None:
            return "Unknown"

        if seconds < 60:
            return f"{seconds} seconds"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"

    def _send_recovery_alert(
        self,
        down_timestamp: Optional[datetime] = None,
        up_timestamp: Optional[datetime] = None,
        downtime_seconds: Optional[int] = None
    ):
        """
        Send a recovery/up alert when subscription is restored.

        Args:
            down_timestamp: When the subscription went down (UTC)
            up_timestamp: When the subscription recovered (UTC)
            downtime_seconds: Total downtime in seconds
        """
        if not self.alert_enabled or not self.alert_recipients or not self.from_email:
            return

        try:
            hostname = socket.gethostname()
            now = up_timestamp or datetime.utcnow()

            # Build timing details section
            timing_details = ""
            if down_timestamp or up_timestamp or downtime_seconds:
                timing_items = []
                if down_timestamp:
                    timing_items.append(f"<strong>Disconnected:</strong> {down_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                if up_timestamp:
                    timing_items.append(f"<strong>Reconnected:</strong> {up_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                if downtime_seconds is not None:
                    timing_items.append(f"<strong>Total Downtime:</strong> {self._format_downtime(downtime_seconds)}")

                timing_details = f"""
                <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; padding: 12px; margin: 16px 0;">
                    <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #475569;">Outage Details</h3>
                    <p style="margin: 0; font-size: 14px;">
                        {'<br/>'.join(timing_items)}
                    </p>
                </div>
                """

            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2 style="color: #16a34a;">✅ Teams Notetaker Recovered</h2>
                <p><strong>Status:</strong> Webhook subscription is now active</p>
                <div style="background: #f0fdf4; border-left: 4px solid #16a34a; padding: 12px; margin: 16px 0;">
                    <p>The Microsoft Graph webhook subscription has been successfully created/restored.</p>
                    <p>Real-time meeting notifications are now working normally.</p>
                </div>
                {timing_details}
                <p style="color: #666; font-size: 12px;">
                    Server: {hostname}<br/>
                    Time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC<br/>
                    Webhook URL: {self.webhook_url}
                </p>
            </body>
            </html>
            """

            for recipient in self.alert_recipients:
                try:
                    endpoint = f"/users/{self.from_email}/sendMail"
                    payload = {
                        "message": {
                            "subject": "[Teams Notetaker] ✅ Webhook Recovered",
                            "body": {
                                "contentType": "HTML",
                                "content": html_body
                            },
                            "toRecipients": [
                                {"emailAddress": {"address": recipient}}
                            ]
                        }
                    }
                    self.graph_client.post(endpoint, json=payload)
                    logger.info(f"Recovery alert email sent to {recipient}")
                except Exception as e:
                    logger.error(f"Failed to send recovery alert to {recipient}: {e}")

        except Exception as e:
            logger.error(f"Failed to send recovery alert email: {e}")

    def _send_alert_email(self, subject: str, body: str, source: str = 'check'):
        """
        Send an alert email to configured recipients.

        Respects cooldown period to avoid spamming.

        Args:
            subject: Email subject
            body: HTML body content
            source: What triggered this alert ('startup', 'check', 'daily_refresh', 'manual')
        """
        if not self.alert_enabled or not self.alert_recipients or not self.from_email:
            logger.warning(f"Alert not sent (enabled={self.alert_enabled}, recipients={len(self.alert_recipients)}, from={self.from_email}): {subject}")
            return

        # Check cooldown
        now = datetime.utcnow()
        if self._last_alert_time:
            hours_since_last = (now - self._last_alert_time).total_seconds() / 3600
            if hours_since_last < self._alert_cooldown_hours:
                logger.info(f"Alert suppressed (cooldown: {hours_since_last:.1f}h < {self._alert_cooldown_hours}h): {subject}")
                return

        try:
            hostname = socket.gethostname()
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2 style="color: #dc2626;">⚠️ Teams Notetaker Alert</h2>
                <p><strong>Issue:</strong> {subject}</p>
                <div style="background: #fef2f2; border-left: 4px solid #dc2626; padding: 12px; margin: 16px 0;">
                    {body}
                </div>
                <p style="color: #666; font-size: 12px;">
                    Server: {hostname}<br/>
                    Time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC<br/>
                    Webhook URL: {self.webhook_url}
                </p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="color: #999; font-size: 11px;">
                    This is an automated alert from Teams Notetaker.
                    You will not receive another alert for {self._alert_cooldown_hours} hours.
                </p>
            </body>
            </html>
            """

            for recipient in self.alert_recipients:
                try:
                    endpoint = f"/users/{self.from_email}/sendMail"
                    payload = {
                        "message": {
                            "subject": f"[Teams Notetaker Alert] {subject}",
                            "body": {
                                "contentType": "HTML",
                                "content": html_body
                            },
                            "toRecipients": [
                                {"emailAddress": {"address": recipient}}
                            ]
                        }
                    }
                    self.graph_client.post(endpoint, json=payload)
                    logger.info(f"Alert email sent to {recipient}: {subject}")
                except Exception as e:
                    logger.error(f"Failed to send alert to {recipient}: {e}")

            self._last_alert_time = now

            # Mark as down and log the down event
            if not self._subscription_down:
                self._subscription_down = True
                self._save_down_state(True)  # Persist across restarts
                self._log_down_event(source, subject)

        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")

    async def start_background_manager(self):
        """
        Start background task that manages subscriptions automatically.

        - Waits for Azure Relay to connect before first subscription attempt
        - Retries with short delays if subscription creation fails
        - Checks every CHECK_INTERVAL_MINUTES for subscription validity
        - Proactively recreates at DAILY_RECREATE_HOUR_UTC
        """
        self.running = True
        logger.info(f"Starting subscription manager (check every {CHECK_INTERVAL_MINUTES}m, recreate daily at {DAILY_RECREATE_HOUR_UTC}:00 UTC)")

        # Wait for Azure Relay listener to connect before first subscription attempt
        logger.info(f"Waiting {STARTUP_DELAY_SECONDS}s for Azure Relay to connect...")
        await asyncio.sleep(STARTUP_DELAY_SECONDS)

        # Initial subscription creation with retries
        # Send recovery alert if we succeed after failures (indicates previous down state)
        if not await self._ensure_subscription_with_retry(source='startup', send_recovery_on_retry_success=True):
            logger.error("⚠️ Failed to create webhook subscription after retries - webhook notifications may not work!")
            self._send_alert_email(
                subject="Webhook Subscription Failed",
                body=f"""
                <p>Failed to create Microsoft Graph webhook subscription after {MAX_CREATION_RETRIES} attempts.</p>
                <p><strong>Impact:</strong> Real-time meeting notifications are NOT working.
                The system will fall back to hourly backfill polling, which means meetings may be processed with up to 1 hour delay.</p>
                <p><strong>Possible causes:</strong></p>
                <ul>
                    <li>Azure Relay connection issues</li>
                    <li>Microsoft Graph API validation timeout</li>
                    <li>Network latency between Graph API and Azure Relay</li>
                </ul>
                <p><strong>Action:</strong> Check the service logs for details. The system will automatically retry when it restarts or at the next scheduled check.</p>
                """,
                source='startup'
            )

        last_daily_recreate: Optional[datetime] = None

        while self.running:
            try:
                now = datetime.utcnow()

                # Check if it's time for daily recreation (within the hour window)
                if now.hour == DAILY_RECREATE_HOUR_UTC:
                    # Only recreate once per day
                    if last_daily_recreate is None or (now - last_daily_recreate).days >= 1:
                        logger.info(f"Daily subscription recreation time ({DAILY_RECREATE_HOUR_UTC}:00 UTC)")
                        if not await self.recreate_subscription('daily_refresh'):
                            self._send_alert_email(
                                subject="Daily Webhook Subscription Refresh Failed",
                                body="""
                                <p>The daily webhook subscription refresh failed.</p>
                                <p><strong>Impact:</strong> Webhook notifications may not work correctly.</p>
                                <p>The system will retry at the next scheduled check in 6 hours.</p>
                                """,
                                source='daily_refresh'
                            )
                        last_daily_recreate = now
                else:
                    # Regular check - ensure subscription exists and is valid
                    if not await self.ensure_subscription('check'):
                        # Try with retries before alerting
                        if not await self._ensure_subscription_with_retry(source='check'):
                            self._send_alert_email(
                                subject="Webhook Subscription Check Failed",
                                body=f"""
                                <p>Periodic subscription check found no valid subscription, and recreation failed after {MAX_CREATION_RETRIES} attempts.</p>
                                <p><strong>Impact:</strong> Real-time meeting notifications are NOT working.</p>
                                <p>The system will retry at the next scheduled check in {CHECK_INTERVAL_MINUTES} minutes.</p>
                                """,
                                source='check'
                            )

                # Sleep until next check
                await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)

            except asyncio.CancelledError:
                logger.info("Subscription manager task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in subscription manager: {e}", exc_info=True)
                # Sleep a bit and retry
                await asyncio.sleep(60)

        logger.info("Subscription manager stopped")

    async def _ensure_subscription_with_retry(
        self,
        source: str = 'check',
        send_recovery_on_retry_success: bool = False
    ) -> bool:
        """
        Ensure subscription exists, retrying if creation fails.

        This handles the race condition at startup where the Azure Relay
        listener may not be fully connected when we first try to create
        a subscription.

        Args:
            source: What triggered this check ('startup', 'check', 'daily_refresh', 'manual')
            send_recovery_on_retry_success: If True, send recovery alert when
                subscription succeeds after initial failure(s)

        Returns:
            True if a valid subscription exists (or was created)
        """
        had_failure = False

        for attempt in range(1, MAX_CREATION_RETRIES + 1):
            if await self.ensure_subscription(source):
                # Check if we should send recovery alert:
                # - Either we had failures during this startup cycle
                # - Or there was a persisted down state from before (already loaded into _subscription_down)
                if send_recovery_on_retry_success and (had_failure or self._subscription_down):
                    logger.info("Subscription active - checking if recovery alert needed")
                    if not self._subscription_down and had_failure:
                        # Had failures this cycle but no persisted state - set it to trigger recovery
                        self._subscription_down = True
                    self._check_and_send_recovery_alert(source)
                return True

            had_failure = True
            if attempt < MAX_CREATION_RETRIES:
                logger.warning(
                    f"Subscription creation attempt {attempt}/{MAX_CREATION_RETRIES} failed, "
                    f"retrying in {RETRY_DELAY_SECONDS}s..."
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

        return False

    def stop(self):
        """Stop the background manager."""
        self.running = False
        if self._check_task:
            self._check_task.cancel()
