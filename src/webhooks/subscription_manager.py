"""
Automatic Microsoft Graph Subscription Manager.

Ensures webhook subscriptions are always active by:
1. Checking on startup and creating if missing
2. Periodically checking and renewing/recreating as needed
3. Proactively recreating daily at a scheduled time
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from ..graph.client import GraphAPIClient
from ..core.config import AppConfig

logger = logging.getLogger(__name__)

# Max expiration for callRecords subscriptions is 4230 minutes (~2.9 days)
# We use 4200 to have a small buffer
CALLRECORDS_MAX_EXPIRATION_MINUTES = 4200

# Renew when less than this many hours remaining
RENEW_THRESHOLD_HOURS = 12

# How often to check subscription status (hours)
CHECK_INTERVAL_HOURS = 6

# Hour of day (UTC) to proactively recreate subscription (3 AM UTC = ~10 PM Eastern)
DAILY_RECREATE_HOUR_UTC = 3


class SubscriptionManager:
    """
    Manages Microsoft Graph webhook subscriptions automatically.

    Ensures subscriptions are always active by checking periodically
    and recreating as needed.
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

    def create_subscription(self) -> Optional[Dict[str, Any]]:
        """
        Create a new callRecords subscription.

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
            response = self.graph_client.post("/subscriptions", json=subscription)

            logger.info(f"✅ Subscription created: {response['id']} (expires: {response['expirationDateTime']})")
            return response

        except Exception as e:
            logger.error(f"Failed to create subscription: {e}")
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

    def renew_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        """
        Renew an existing subscription.

        Args:
            subscription_id: Subscription ID to renew

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
            return response

        except Exception as e:
            logger.error(f"Failed to renew subscription {subscription_id}: {e}")
            return None

    def ensure_subscription(self) -> bool:
        """
        Ensure at least one valid callRecords subscription exists.

        Checks for existing subscriptions and creates one if:
        - No subscriptions exist
        - All existing subscriptions are expired or expiring soon

        Returns:
            True if a valid subscription exists (or was created)
        """
        logger.info("Checking callRecords subscription status...")

        subscriptions = self.get_callrecords_subscriptions()

        if not subscriptions:
            logger.warning("No callRecords subscriptions found, creating one...")
            return self.create_subscription() is not None

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
                    return True
                else:
                    # Subscription expiring soon, try to renew
                    hours_remaining = (expiry - now).total_seconds() / 3600
                    logger.warning(f"Subscription expiring soon ({hours_remaining:.1f}h), renewing...")

                    if self.renew_subscription(sub["id"]):
                        return True
                    else:
                        # Renewal failed, try to delete and create new
                        logger.warning("Renewal failed, recreating subscription...")
                        self.delete_subscription(sub["id"])
                        return self.create_subscription() is not None

            except Exception as e:
                logger.error(f"Error parsing subscription expiry: {e}")
                continue

        # All subscriptions are invalid, create new one
        logger.warning("No valid subscriptions found, creating new one...")
        return self.create_subscription() is not None

    def recreate_subscription(self) -> bool:
        """
        Delete all existing subscriptions and create a fresh one.

        Used for daily proactive recreation to ensure clean state.

        Returns:
            True if new subscription was created successfully
        """
        logger.info("Proactively recreating callRecords subscription...")

        # Delete all existing subscriptions
        subscriptions = self.get_callrecords_subscriptions()
        for sub in subscriptions:
            self.delete_subscription(sub["id"])

        # Create fresh subscription
        return self.create_subscription() is not None

    async def start_background_manager(self):
        """
        Start background task that manages subscriptions automatically.

        - Checks every CHECK_INTERVAL_HOURS for subscription validity
        - Proactively recreates at DAILY_RECREATE_HOUR_UTC
        """
        self.running = True
        logger.info(f"Starting subscription manager (check every {CHECK_INTERVAL_HOURS}h, recreate daily at {DAILY_RECREATE_HOUR_UTC}:00 UTC)")

        last_daily_recreate: Optional[datetime] = None

        while self.running:
            try:
                now = datetime.utcnow()

                # Check if it's time for daily recreation (within the hour window)
                if now.hour == DAILY_RECREATE_HOUR_UTC:
                    # Only recreate once per day
                    if last_daily_recreate is None or (now - last_daily_recreate).days >= 1:
                        logger.info(f"Daily subscription recreation time ({DAILY_RECREATE_HOUR_UTC}:00 UTC)")
                        self.recreate_subscription()
                        last_daily_recreate = now
                else:
                    # Regular check - ensure subscription exists and is valid
                    self.ensure_subscription()

                # Sleep until next check
                await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)

            except asyncio.CancelledError:
                logger.info("Subscription manager task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in subscription manager: {e}", exc_info=True)
                # Sleep a bit and retry
                await asyncio.sleep(60)

        logger.info("Subscription manager stopped")

    def stop(self):
        """Stop the background manager."""
        self.running = False
        if self._check_task:
            self._check_task.cancel()
