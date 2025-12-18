"""
CLI commands for webhook management (Azure Relay integration).
"""

import click
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.config import get_config
from src.core.database import DatabaseManager
from src.graph.client import GraphAPIClient
from src.webhooks.azure_relay_listener import AzureRelayWebhookListener
from src.webhooks.call_records_handler import CallRecordsWebhookHandler


@click.group()
def webhooks():
    """Manage Microsoft Graph webhooks via Azure Relay."""
    pass


@webhooks.command("listen")
@click.option("--backfill/--no-backfill", default=True, help="Backfill recent meetings on startup")
def listen_command(backfill):
    """
    Start Azure Relay webhook listener.

    Connects to Azure Relay and listens for Microsoft Graph webhook notifications.
    Processes org-wide meeting callRecords for opted-in users.

    Example:
        python -m src.main webhooks listen
        python -m src.main webhooks listen --no-backfill
    """
    asyncio.run(start_webhook_listener(backfill=backfill))


async def start_webhook_listener(backfill: bool = True):
    """Start the Azure Relay webhook listener."""
    config = get_config()

    # Validate Azure Relay configuration
    if not config.azure_relay.is_configured():
        click.echo("❌ Azure Relay not configured!", err=True)
        click.echo("\nAdd to your .env file:")
        click.echo("  AZURE_RELAY_NAMESPACE=your-relay.servicebus.windows.net")
        click.echo("  AZURE_RELAY_HYBRID_CONNECTION=teams-webhooks")
        click.echo("  AZURE_RELAY_KEY=<your-key>")
        click.echo("\nSee AZURE_RELAY_SETUP.md for complete instructions.")
        sys.exit(1)

    click.echo("🔐 Azure Relay Webhook Listener")
    click.echo("=" * 80)
    click.echo(f"Namespace: {config.azure_relay.namespace}")
    click.echo(f"Hybrid Connection: {config.azure_relay.hybrid_connection}")
    click.echo(f"Webhook URL: {config.azure_relay.webhook_url}")
    click.echo("=" * 80)
    click.echo()

    # Initialize components
    db = DatabaseManager(config.database.connection_string)
    graph_client = GraphAPIClient(config.graph_api)
    handler = CallRecordsWebhookHandler(db, graph_client)

    # Backfill recent meetings
    if backfill and config.app.webhook_backfill_hours > 0:
        click.echo(f"📊 Backfilling meetings from last {config.app.webhook_backfill_hours} hours...")
        try:
            await handler.backfill_recent_meetings(config.app.webhook_backfill_hours)
            click.echo("✅ Backfill complete")
            click.echo()
        except Exception as e:
            click.echo(f"⚠️  Backfill failed: {e}")
            click.echo()

    # Create Azure Relay listener
    listener = AzureRelayWebhookListener(
        relay_namespace=config.azure_relay.namespace,
        hybrid_connection_name=config.azure_relay.hybrid_connection,
        shared_access_key_name=config.azure_relay.key_name,
        shared_access_key=config.azure_relay.key
    )

    # Start listening
    click.echo("🎧 Starting listener...")
    click.echo("   Press Ctrl+C to stop")
    click.echo()

    try:
        await listener.start(callback=handler.handle_notification)
    except KeyboardInterrupt:
        click.echo("\n\n🛑 Stopping listener...")
        await listener.stop()
        click.echo("✅ Listener stopped")


@webhooks.command("subscribe")
@click.option("--expiration-days", default=180, help="Subscription expiration in days (max 180)")
def subscribe_command(expiration_days):
    """
    Create Microsoft Graph subscription for callRecords.

    This subscribes to org-wide meeting notifications via your Azure Relay endpoint.

    Example:
        python -m src.main webhooks subscribe
        python -m src.main webhooks subscribe --expiration-days 90
    """
    config = get_config()

    if not config.azure_relay.is_configured():
        click.echo("❌ Azure Relay not configured!", err=True)
        sys.exit(1)

    click.echo("📡 Creating Microsoft Graph Subscription")
    click.echo("=" * 80)

    try:
        from datetime import datetime, timedelta
        import requests

        graph_client = GraphAPIClient(config.graph_api)

        # Calculate expiration (max 180 days for callRecords)
        expiry = datetime.utcnow() + timedelta(days=min(expiration_days, 180))

        # Build subscription
        subscription = {
            "changeType": "created",
            "notificationUrl": config.azure_relay.webhook_url,  # No path suffix - Azure Relay doesn't support path routing
            "resource": "/communications/callRecords",
            "expirationDateTime": expiry.isoformat() + "Z",
            "clientState": "teams-notetaker-secret"
        }

        click.echo(f"Resource: {subscription['resource']}")
        click.echo(f"Notification URL: {subscription['notificationUrl']}")
        click.echo(f"Expiration: {expiry.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        click.echo()

        # Create subscription
        click.echo("Creating subscription...")
        response = graph_client.post("/subscriptions", json=subscription)

        click.echo()
        click.echo("✅ Subscription created successfully!")
        click.echo("=" * 80)
        click.echo(f"Subscription ID: {response['id']}")
        click.echo(f"Expires: {response['expirationDateTime']}")
        click.echo()
        click.echo("💾 Save this subscription ID to renew it before expiration:")
        click.echo(f"   python -m src.main webhooks renew --subscription-id {response['id']}")
        click.echo()

    except Exception as e:
        click.echo(f"\n❌ Failed to create subscription: {e}", err=True)
        click.echo()
        click.echo("Make sure:")
        click.echo("  1. Azure Relay listener is running")
        click.echo("  2. Graph API has CallRecords.Read.All permission")
        click.echo("  3. Permission is admin-consented")
        sys.exit(1)


@webhooks.command("subscribe-transcripts")
@click.option("--expiration-minutes", default=60, help="Subscription expiration in minutes (max 4230, use <=60 to avoid lifecycle URL requirement)")
def subscribe_transcripts_command(expiration_minutes):
    """
    Create Microsoft Graph subscription for callTranscripts.

    This subscribes to transcript-ready notifications via your Azure Relay endpoint.
    Only fires when transcripts are READY (not for every meeting).

    NOTE: Expiration > 60 minutes requires lifecycleNotificationUrl.
          Recommended: Use 60 minutes or less and set up automatic renewal.

    Example:
        python -m src.main webhooks subscribe-transcripts
        python -m src.main webhooks subscribe-transcripts --expiration-minutes 30
    """
    config = get_config()

    if not config.azure_relay.is_configured():
        click.echo("❌ Azure Relay not configured!", err=True)
        sys.exit(1)

    click.echo("📡 Creating Microsoft Graph Transcript Subscription")
    click.echo("=" * 80)

    try:
        from datetime import datetime, timedelta

        graph_client = GraphAPIClient(config.graph_api)

        # Calculate expiration (max 4230 minutes, but >60 requires lifecycleNotificationUrl)
        expiry = datetime.utcnow() + timedelta(minutes=min(expiration_minutes, 4230))

        # Build subscription
        subscription = {
            "changeType": "created",
            "notificationUrl": config.azure_relay.webhook_url,
            "resource": "communications/onlineMeetings/getAllTranscripts",
            "expirationDateTime": expiry.isoformat() + "Z",
            "clientState": "teams-notetaker-secret"
        }

        # Add lifecycleNotificationUrl if expiration > 1 hour
        if expiration_minutes > 60:
            subscription["lifecycleNotificationUrl"] = config.azure_relay.webhook_url
            click.echo(f"⚠️  Expiration > 60 minutes, adding lifecycleNotificationUrl")
            click.echo()

        click.echo(f"Resource: {subscription['resource']}")
        click.echo(f"Notification URL: {subscription['notificationUrl']}")
        click.echo(f"Expiration: {expiry.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        click.echo()
        click.echo("⚡ This subscription only fires when transcripts are READY")
        click.echo("   (No polling needed, completely event-driven!)")
        click.echo()

        # Create subscription
        click.echo("Creating subscription...")
        response = graph_client.post("/subscriptions", json=subscription)

        click.echo()
        click.echo("✅ Subscription created successfully!")
        click.echo("=" * 80)
        click.echo(f"Subscription ID: {response['id']}")
        click.echo(f"Expires: {response['expirationDateTime']}")
        click.echo()
        click.echo("⚠️  IMPORTANT: Transcript subscriptions expire in 24 hours!")
        click.echo("   Set up automatic renewal with systemd timer:")
        click.echo("   See DEPLOYMENT.md for systemd timer setup instructions")
        click.echo()

    except Exception as e:
        click.echo(f"\n❌ Failed to create subscription: {e}", err=True)
        click.echo()
        click.echo("Make sure:")
        click.echo("  1. Azure Relay listener is running")
        click.echo("  2. Graph API has OnlineMeetingTranscript.Read.All permission")
        click.echo("  3. Permission is admin-consented")
        sys.exit(1)


@webhooks.command("renew-all")
@click.option("--min-hours-remaining", default=12, help="Renew subscriptions expiring within N hours")
def renew_all_command(min_hours_remaining):
    """
    Renew all Microsoft Graph subscriptions that are expiring soon.

    This command checks all active subscriptions and renews any that are
    expiring within the specified time window (default: 12 hours).

    Useful for automated renewal via systemd timers or cron jobs.

    Example:
        python -m src.main webhooks renew-all
        python -m src.main webhooks renew-all --min-hours-remaining 6
    """
    config = get_config()
    graph_client = GraphAPIClient(config.graph_api)

    click.echo("🔄 Renewing Microsoft Graph Subscriptions")
    click.echo("=" * 80)
    click.echo()

    try:
        from datetime import datetime, timedelta

        # Get all subscriptions
        response = graph_client.get("/subscriptions")
        subscriptions = response.get("value", [])

        if not subscriptions:
            click.echo("No active subscriptions found.")
            return

        now = datetime.utcnow()
        threshold = now + timedelta(hours=min_hours_remaining)
        renewed_count = 0
        skipped_count = 0

        for sub in subscriptions:
            sub_id = sub['id']
            resource = sub['resource']
            expiry_str = sub['expirationDateTime']

            # Parse expiration datetime (remove 'Z' suffix and parse)
            expiry = datetime.fromisoformat(expiry_str.replace('Z', ''))

            # Calculate time remaining
            time_remaining = expiry - now
            hours_remaining = time_remaining.total_seconds() / 3600

            click.echo(f"📋 {resource[:50]}...")
            click.echo(f"   ID: {sub_id[:30]}...")
            click.echo(f"   Expires: {expiry_str} ({hours_remaining:.1f}h remaining)")

            if expiry <= threshold:
                click.echo(f"   ⚠️  Expiring soon, renewing...")

                # Determine new expiration based on resource type
                if "transcript" in resource.lower():
                    # Transcript subscriptions: use 60 minutes to avoid lifecycleNotificationUrl requirement
                    new_expiry = now + timedelta(minutes=60)
                else:
                    # CallRecords subscriptions: max 180 days
                    new_expiry = now + timedelta(days=180)

                # Renew subscription
                update_payload = {
                    "expirationDateTime": new_expiry.isoformat() + "Z"
                }

                try:
                    renewed = graph_client.patch(f"/subscriptions/{sub_id}", json=update_payload)
                    click.echo(f"   ✅ Renewed until {renewed['expirationDateTime']}")
                    renewed_count += 1
                except Exception as e:
                    click.echo(f"   ❌ Failed to renew: {e}")
            else:
                click.echo(f"   ✓  No renewal needed")
                skipped_count += 1

            click.echo()

        click.echo("=" * 80)
        click.echo(f"✅ Renewal complete:")
        click.echo(f"   Renewed: {renewed_count}")
        click.echo(f"   Skipped: {skipped_count}")
        click.echo()

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@webhooks.command("list")
def list_subscriptions():
    """List active Microsoft Graph subscriptions."""
    config = get_config()
    graph_client = GraphAPIClient(config.graph_api)

    click.echo("📋 Active Microsoft Graph Subscriptions")
    click.echo("=" * 80)

    try:
        response = graph_client.get("/subscriptions")
        subscriptions = response.get("value", [])

        if not subscriptions:
            click.echo("No active subscriptions found.")
            return

        for sub in subscriptions:
            click.echo()
            click.echo(f"ID: {sub['id']}")
            click.echo(f"Resource: {sub['resource']}")
            click.echo(f"Change Type: {sub['changeType']}")
            click.echo(f"Notification URL: {sub['notificationUrl']}")
            click.echo(f"Expires: {sub['expirationDateTime']}")
            click.echo("-" * 80)

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@webhooks.command("delete")
@click.argument("subscription_id")
def delete_subscription(subscription_id):
    """Delete a Microsoft Graph subscription."""
    config = get_config()
    graph_client = GraphAPIClient(config.graph_api)

    click.echo(f"🗑️  Deleting subscription {subscription_id}...")

    try:
        graph_client.delete(f"/subscriptions/{subscription_id}")
        click.echo("✅ Subscription deleted")
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@webhooks.command("test")
def test_setup():
    """Test Azure Relay configuration and connectivity."""
    config = get_config()

    click.echo("🧪 Testing Azure Relay Setup")
    click.echo("=" * 80)
    click.echo()

    # Check configuration
    click.echo("1. Checking configuration...")
    if config.azure_relay.is_configured():
        click.echo("   ✅ Azure Relay configured")
        click.echo(f"      Namespace: {config.azure_relay.namespace}")
        click.echo(f"      Hybrid Connection: {config.azure_relay.hybrid_connection}")
        click.echo(f"      Webhook URL: {config.azure_relay.webhook_url}")
    else:
        click.echo("   ❌ Azure Relay not configured")
        click.echo("      Add credentials to .env file")
        return

    click.echo()

    # Check database
    click.echo("2. Checking database...")
    try:
        db = DatabaseManager(config.database.connection_string)
        with db.get_session() as session:
            from src.core.database import ProcessedCallRecord
            count = session.query(ProcessedCallRecord).count()
            click.echo(f"   ✅ Database connected")
            click.echo(f"      Processed call records: {count}")
    except Exception as e:
        click.echo(f"   ❌ Database error: {e}")
        return

    click.echo()

    # Check Graph API
    click.echo("3. Checking Graph API...")
    try:
        graph_client = GraphAPIClient(config.graph_api)
        graph_client.test_connection()
        click.echo("   ✅ Graph API connected")
    except Exception as e:
        click.echo(f"   ❌ Graph API error: {e}")
        return

    click.echo()
    click.echo("=" * 80)
    click.echo("✅ All checks passed!")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. python -m src.main webhooks listen     # Start listener")
    click.echo("  2. python -m src.main webhooks subscribe  # Create subscription")
    click.echo("  3. Have a test meeting to verify!")


if __name__ == "__main__":
    webhooks()
