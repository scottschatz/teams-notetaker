"""
Teams Meeting Transcript Summarizer - CLI Entry Point

Command-line interface for managing the Teams notetaker application.
"""

import click
import sys
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_config, ConfigManager
from src.core.database import DatabaseManager
from src.core.logging_config import setup_logging
from src.cli.webhooks_commands import webhooks


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--log-file", type=str, help="Log file path")
@click.pass_context
def cli(ctx, verbose, log_file):
    """Teams Meeting Transcript Summarizer CLI.

    AI-powered meeting summary and distribution system for Microsoft Teams.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Set up logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(log_level=log_level, log_file=log_file)

    # Store config in context
    ctx.obj["verbose"] = verbose
    ctx.obj["log_file"] = log_file


# ============================================================================
# MAIN OPERATIONS
# ============================================================================


@cli.command()
@click.option("--loop", is_flag=True, help="Run continuously (poller + worker)")
@click.option("--dry-run", is_flag=True, help="Discover meetings but don't enqueue jobs")
@click.option("--discover-only", is_flag=True, help="Only discover meetings, don't process")
@click.option("--meeting-id", type=str, help="Process specific meeting by Graph ID")
@click.option("--user-email", type=str, help="Process all meetings for specific user")
@click.option("--local-transcript", type=click.Path(exists=True), help="Test with local VTT file")
@click.option("--interval", type=int, help="Custom polling interval in seconds (for --loop)")
def run(loop, dry_run, discover_only, meeting_id, user_email, local_transcript, interval):
    """Run poller and worker to process meetings.

    Examples:
        python -m src.main run                    # Single discovery cycle
        python -m src.main run --loop             # Run continuously
        python -m src.main run --dry-run          # Test without processing
        python -m src.main run --meeting-id <id>  # Process specific meeting
    """
    click.echo("🚀 Starting Teams Meeting Transcript Summarizer")

    if loop:
        click.echo(f"📡 Running in continuous mode (interval: {interval or 'from config'} seconds)")
        click.echo("⚠️  Press Ctrl+C to stop")
        # TODO: Implement continuous polling
        click.echo("❌ --loop mode not yet implemented (coming in Phase 7)")
    elif discover_only:
        click.echo("🔍 Discovery mode: Finding meetings...")
        # TODO: Implement discovery only
        click.echo("❌ --discover-only not yet implemented (coming in Phase 7)")
    elif meeting_id:
        click.echo(f"🎯 Processing specific meeting: {meeting_id}")
        # TODO: Implement single meeting processing
        click.echo("❌ --meeting-id not yet implemented (coming in Phase 6)")
    elif user_email:
        click.echo(f"👤 Processing meetings for user: {user_email}")
        # TODO: Implement user-specific processing
        click.echo("❌ --user-email not yet implemented (coming in Phase 7)")
    elif local_transcript:
        click.echo(f"📄 Testing with local transcript: {local_transcript}")
        # TODO: Implement local transcript testing
        click.echo("❌ --local-transcript not yet implemented (coming in Phase 5)")
    else:
        click.echo("▶️  Running single discovery cycle")
        # TODO: Implement single discovery cycle
        click.echo("❌ Single run not yet implemented (coming in Phase 7)")


@cli.command()
@click.option("--host", default="0.0.0.0", help="Web server host")
@click.option("--port", default=8000, type=int, help="Web server port")
def serve(host, port):
    """Start web dashboard.

    Example:
        python -m src.main serve --port 8000
    """
    click.echo(f"🌐 Starting web dashboard on {host}:{port}")
    click.echo("❌ Web dashboard not yet implemented (coming in Phase 8)")
    # TODO: Implement web dashboard startup
    # import uvicorn
    # from src.web.app import create_app
    # app = create_app()
    # uvicorn.run(app, host=host, port=port)


@cli.command()
def start_all():
    """Start both poller/worker and web dashboard.

    This starts both services in separate processes.
    """
    click.echo("🚀 Starting all services (poller + web)")
    click.echo("❌ start-all not yet implemented (coming in Phase 11)")
    # TODO: Implement starting both services


# ============================================================================
# PILOT USER MANAGEMENT
# ============================================================================


@cli.group()
def pilot():
    """Manage pilot users."""
    pass


@pilot.command("add")
@click.argument("email")
@click.option("--name", help="User display name")
@click.option("--notes", help="Notes about this user")
@click.option("--added-by", default="admin", help="Who added this user")
def pilot_add(email, name, notes, added_by):
    """Add user to pilot program.

    Example:
        python -m src.main pilot add scott@townsquaremedia.com --name "Scott Schatz"
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        user = db.add_pilot_user(email=email, display_name=name, notes=notes, added_by=added_by)

        click.echo(f"✅ Added {email} to pilot program (ID: {user.id})")
        if name:
            click.echo(f"   Name: {name}")
        if notes:
            click.echo(f"   Notes: {notes}")

    except Exception as e:
        click.echo(f"❌ Failed to add pilot user: {e}", err=True)
        sys.exit(1)


@pilot.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show inactive users too")
def pilot_list(show_all):
    """List pilot users.

    Example:
        python -m src.main pilot list
        python -m src.main pilot list --all
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        users = db.get_pilot_users(active_only=not show_all)

        if not users:
            click.echo("No pilot users found")
            return

        click.echo(f"\n📋 Pilot Users ({len(users)}):")
        click.echo("-" * 80)

        for user in users:
            status = "✅ Active" if user.is_active else "❌ Inactive"
            click.echo(f"\n{status}")
            click.echo(f"  Email: {user.email}")
            if user.display_name:
                click.echo(f"  Name:  {user.display_name}")
            click.echo(f"  Added: {user.added_at.strftime('%Y-%m-%d %H:%M')}")
            if user.added_by:
                click.echo(f"  By:    {user.added_by}")
            if user.notes:
                click.echo(f"  Notes: {user.notes}")

        click.echo("")

    except Exception as e:
        click.echo(f"❌ Failed to list pilot users: {e}", err=True)
        sys.exit(1)


@pilot.command("remove")
@click.argument("email")
def pilot_remove(email):
    """Remove user from pilot program (mark inactive).

    Example:
        python -m src.main pilot remove user@townsquaremedia.com
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        from src.core.database import PilotUser

        session = db.get_session()
        try:
            user = session.query(PilotUser).filter(PilotUser.email == email.lower()).first()

            if not user:
                click.echo(f"❌ User {email} not found in pilot program", err=True)
                sys.exit(1)

            if not user.is_active:
                click.echo(f"⚠️  User {email} is already inactive")
                return

            user.is_active = False
            session.commit()

            click.echo(f"✅ Removed {email} from pilot program")

        finally:
            session.close()

    except Exception as e:
        click.echo(f"❌ Failed to remove pilot user: {e}", err=True)
        sys.exit(1)


# ============================================================================
# DATABASE MANAGEMENT
# ============================================================================


@cli.group()
def db():
    """Database management commands."""
    pass


@db.command("init")
@click.option("--drop", is_flag=True, help="Drop existing tables first (DESTRUCTIVE!)")
def db_init(drop):
    """Initialize database schema.

    Creates all tables defined in the SQLAlchemy models.

    Example:
        python -m src.main db init
        python -m src.main db init --drop  # Recreate all tables
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        if drop:
            if not click.confirm("⚠️  This will drop all existing tables. Are you sure?"):
                click.echo("Aborted.")
                return
            click.echo("🗑️  Dropping existing tables...")
            db.drop_tables()

        click.echo("📦 Creating database tables...")
        db.create_tables()

        click.echo("✅ Database initialized successfully")

    except Exception as e:
        click.echo(f"❌ Failed to initialize database: {e}", err=True)
        sys.exit(1)


@db.command("seed-config")
def db_seed_config():
    """Seed default configuration values.

    Populates the app_config table with default settings.

    Example:
        python -m src.main db seed-config
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        click.echo("🌱 Seeding default configuration...")
        db.seed_default_config()

        click.echo("✅ Configuration seeded successfully")

    except Exception as e:
        click.echo(f"❌ Failed to seed configuration: {e}", err=True)
        sys.exit(1)


@db.command("migrate")
def db_migrate():
    """Run database migrations (Alembic).

    Example:
        python -m src.main db migrate
    """
    click.echo("🔄 Running database migrations...")
    click.echo("❌ Migrations not yet implemented (coming in Phase 10)")
    # TODO: Implement Alembic migrations
    # import subprocess
    # subprocess.run(['alembic', 'upgrade', 'head'])


@db.command("status")
def db_status():
    """Show database status and statistics.

    Example:
        python -m src.main db status
    """
    try:
        config = get_config()
        db = DatabaseManager(config.database.connection_string)

        stats = db.get_dashboard_stats()

        click.echo("\n📊 Database Status")
        click.echo("=" * 80)
        click.echo(f"\nTotal Meetings: {stats['total_meetings']}")

        if stats["meeting_status"]:
            click.echo("\nMeetings by Status:")
            for status, count in stats["meeting_status"].items():
                click.echo(f"  {status}: {count}")

        if stats["job_status"]:
            click.echo("\nJobs by Status:")
            for status, count in stats["job_status"].items():
                click.echo(f"  {status}: {count}")

        click.echo("\n" + "=" * 80 + "\n")

    except Exception as e:
        click.echo(f"❌ Failed to get database status: {e}", err=True)
        sys.exit(1)


# ============================================================================
# HEALTH CHECKS
# ============================================================================


@cli.command()
def health():
    """Test system connections (Database, Graph API, Claude API).

    Example:
        python -m src.main health
    """
    click.echo("\n🏥 Testing System Health")
    click.echo("=" * 80)

    config = get_config()
    all_healthy = True

    # Test Database
    click.echo("\n📦 Database Connection...")
    try:
        from sqlalchemy import text
        db = DatabaseManager(config.database.connection_string)
        session = db.get_session()
        session.execute(text("SELECT 1"))
        session.close()
        click.echo("   ✅ Database: Connected")
    except Exception as e:
        click.echo(f"   ❌ Database: Failed ({e})")
        all_healthy = False

    # Test Graph API
    click.echo("\n📡 Microsoft Graph API...")
    try:
        if not config.graph_api.client_id or not config.graph_api.client_secret:
            click.echo("   ⚠️  Graph API: Credentials not configured")
            all_healthy = False
        else:
            from src.graph.client import GraphAPIClient
            client = GraphAPIClient(config.graph_api)
            client.test_connection()
            click.echo("   ✅ Graph API: Connected")
    except Exception as e:
        click.echo(f"   ❌ Graph API: Failed ({e})")
        all_healthy = False

    # Test Claude API
    click.echo("\n🤖 Claude API...")
    try:
        if not config.claude.api_key or config.claude.api_key == "your-api-key-here":
            click.echo("   ⚠️  Claude API: API key not configured")
            # Don't mark as unhealthy - Claude is optional for basic operation
        else:
            # Don't actually test Claude API (costs money)
            click.echo("   ✅ Claude API: Configured")
            click.echo(f"      Model: {config.claude.model}")
    except Exception as e:
        click.echo(f"   ❌ Claude API: Failed ({e})")
        all_healthy = False

    # Overall status
    click.echo("\n" + "=" * 80)
    if all_healthy:
        click.echo("✅ All systems operational")
        click.echo("")
        sys.exit(0)
    else:
        click.echo("⚠️  Some systems have issues")
        click.echo("")
        sys.exit(1)


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================


@cli.group()
def config():
    """Configuration management."""
    pass


@config.command("show")
def config_show():
    """Show current configuration (non-sensitive).

    Example:
        python -m src.main config show
    """
    cfg = get_config()

    click.echo("\n⚙️  Current Configuration")
    click.echo("=" * 80)

    click.echo("\n📊 Runtime Settings (config.yaml):")
    click.echo(f"  Polling Interval: {cfg.app.polling_interval_minutes} minutes")
    click.echo(f"  Lookback Hours: {cfg.app.lookback_hours}")
    click.echo(f"  Pilot Mode: {'Enabled' if cfg.app.pilot_mode_enabled else 'Disabled'}")
    click.echo(f"  Max Concurrent Jobs: {cfg.app.max_concurrent_jobs}")
    click.echo(f"  Job Timeout: {cfg.app.job_timeout_minutes} minutes")
    click.echo(f"  Email Distribution: {'Enabled' if cfg.app.email_enabled else 'Disabled'}")
    click.echo(f"  Teams Chat: {'Enabled' if cfg.app.teams_chat_enabled else 'Disabled'}")

    click.echo("\n🔐 Credentials Status (.env):")
    click.echo(f"  Graph API: {'Configured' if cfg.graph_api.client_id else 'Not set'}")
    click.echo(f"  Claude API: {'Configured' if cfg.claude.api_key else 'Not set'}")
    click.echo(f"  Database: {'Configured' if cfg.database.password else 'Not set'}")
    click.echo(f"  Azure AD SSO: {'Enabled' if cfg.azure_ad.enabled else 'Disabled'}")

    click.echo("\n" + "=" * 80 + "\n")


@config.command("validate")
def config_validate():
    """Validate configuration.

    Example:
        python -m src.main config validate
    """
    cfg = get_config()
    errors = cfg.validate()

    click.echo("\n🔍 Validating Configuration")
    click.echo("=" * 80)

    if not errors:
        click.echo("\n✅ Configuration is valid")
        click.echo("")
        sys.exit(0)
    else:
        click.echo("\n❌ Configuration has errors:")
        for error in errors:
            click.echo(f"   • {error}")
        click.echo("")
        sys.exit(1)


# ============================================================================
# WEBHOOK MANAGEMENT
# ============================================================================

# Register webhooks command group
cli.add_command(webhooks)


# ============================================================================
# MAIN OPERATIONS
# ============================================================================


async def _run_consolidated_service(config, db, graph_client, handler):
    """
    Run the consolidated service: webhook listener + job worker.

    Both run concurrently in the same async event loop.
    """
    import asyncio
    import signal
    from src.jobs.worker import JobWorker
    from src.webhooks.azure_relay_listener import AzureRelayWebhookListener
    from src.webhooks.subscription_manager import SubscriptionManager

    print("🔗 Starting webhook listener...")

    # Check if Azure Relay is configured
    if not config.azure_relay.is_configured():
        print("⚠️  Azure Relay not configured - running worker only (no real-time webhooks)")
        print("   Add AZURE_RELAY_* settings to .env for real-time discovery")
        print("")

        # Just run the worker
        worker = JobWorker(
            config=config,
            db=db,
            max_concurrent=config.app.max_concurrent_jobs,
            job_timeout=config.app.job_timeout_minutes * 60
        )
        await worker.start()
        return

    # Create Azure Relay listener
    listener = AzureRelayWebhookListener(
        relay_namespace=config.azure_relay.namespace,
        hybrid_connection_name=config.azure_relay.hybrid_connection,
        shared_access_key_name=config.azure_relay.key_name,
        shared_access_key=config.azure_relay.key
    )

    # Subscription manager (auto-renews webhook subscriptions)
    sub_manager = SubscriptionManager(config, graph_client)

    print(f"   Namespace: {config.azure_relay.namespace}")
    print(f"   Webhook URL: {config.azure_relay.webhook_url}")

    # Ensure subscription is active
    print("📡 Ensuring webhook subscription is active...")
    if sub_manager.ensure_subscription():
        print("   ✅ Webhook subscription active")
    else:
        print("   ⚠️  Could not ensure subscription (will retry)")
    print("")

    # Create worker
    print("👷 Starting job worker...")
    worker = JobWorker(
        config=config,
        db=db,
        max_concurrent=config.app.max_concurrent_jobs,
        job_timeout=config.app.job_timeout_minutes * 60
    )
    print(f"   Worker ID: {worker.worker_id}")
    print(f"   Max concurrent jobs: {config.app.max_concurrent_jobs}")
    print("")

    print("🎧 Listening for webhooks... (Ctrl+C to stop)")
    print("📅 Periodic backfill: every hour with 2h lookback")
    print("")

    # Periodic backfill to catch missed webhooks (runs every hour)
    async def periodic_backfill():
        """Run backfill every hour to catch missed webhook notifications."""
        import logging
        logger = logging.getLogger(__name__)

        while True:
            await asyncio.sleep(3600)  # Wait 1 hour
            try:
                logger.info("🔄 Running hourly backfill (2h lookback)...")
                stats = await handler.backfill_recent_meetings(lookback_hours=2)
                logger.info(f"✅ Hourly backfill: {stats['call_records_found']} records, "
                          f"{stats['meetings_created']} new, {stats['skipped_no_optin']} skipped")
            except Exception as e:
                logger.error(f"Hourly backfill error: {e}")

    # Run all components concurrently
    try:
        await asyncio.gather(
            listener.start(callback=handler.handle_notification),
            worker.start(),
            sub_manager.start_background_manager(),
            periodic_backfill(),  # NEW: hourly safety net backfill
        )
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        sub_manager.stop()
        await worker.stop()
        await listener.stop()
        print("✅ Service stopped")


@cli.command("start")
@click.option("--service", is_flag=True, default=True, help="Run as consolidated service (webhook listener + worker)")
@click.option("--poll-loop", is_flag=True, help="Run continuous polling loop (legacy mode, no webhooks)")
@click.option("--interval", type=int, help="Polling interval in minutes (for --poll-loop)")
@click.option("--dry-run", is_flag=True, help="Discover meetings but don't enqueue")
@click.option("--skip-backfill", is_flag=True, help="Skip initial backfill discovery")
def start(service, poll_loop, interval, dry_run, skip_backfill):
    """Start the Teams Notetaker service.

    Example:
        python -m src.main start                  # Start consolidated service (default)
        python -m src.main start --skip-backfill  # Start without backfill
        python -m src.main start --poll-loop      # Legacy polling mode (no webhooks)
    """
    from src.discovery.poller import MeetingPoller
    from src.jobs.worker import JobWorker
    import asyncio
    import threading

    config = get_config()

    if service and not poll_loop:
        # Consolidated service: backfill + webhook listener + worker (all-in-one)
        click.echo("🚀 Starting Teams Notetaker Service")
        click.echo("   • Webhook listener for real-time meeting discovery")
        click.echo("   • Job worker for transcript/summary processing")
        click.echo("   Press Ctrl+C to stop")
        click.echo("")

        from src.webhooks.call_records_handler import CallRecordsWebhookHandler
        from src.graph.client import GraphAPIClient
        from src.core.database import DatabaseManager

        db = DatabaseManager(config.database.connection_string)
        graph_client = GraphAPIClient(config.graph_api)
        handler = CallRecordsWebhookHandler(db, graph_client)

        # Run one-time backfill using callRecords API (org-wide)
        # NO CAP - goes back to last successful webhook, catches everything missed
        if not skip_backfill:
            click.echo("📋 Running org-wide callRecords backfill...")

            # Calculate hours since last successful processing (no cap)
            from src.cli.webhooks_commands import get_smart_backfill_hours
            backfill_hours = asyncio.run(get_smart_backfill_hours(db, config))

            if backfill_hours > 0:
                click.echo(f"   Looking back {backfill_hours} hours to last processed meeting...")
                stats = asyncio.run(handler.backfill_recent_meetings(
                    lookback_hours=backfill_hours
                ))
                click.echo(f"   ✅ Backfill: {stats['call_records_found']} callRecords, "
                          f"{stats['meetings_created']} new meetings, "
                          f"{stats['jobs_created']} jobs, "
                          f"{stats['skipped_no_optin']} skipped (no opt-in)")
            else:
                click.echo("   ✅ No backfill needed (processed within last hour)")
            click.echo("")

        # Start consolidated service (webhook listener + worker)
        asyncio.run(_run_consolidated_service(config, db, graph_client, handler))

    elif poll_loop:
        # Legacy mode: continuous polling (for environments without webhooks)
        click.echo("🚀 Starting continuous polling mode (legacy)")
        click.echo("   Press Ctrl+C to stop")
        click.echo("")

        # Start worker in background thread
        worker = JobWorker(
            config=config,
            max_concurrent=config.app.max_concurrent_jobs,
            job_timeout=config.app.job_timeout_minutes * 60
        )

        worker_thread = threading.Thread(target=worker.run, daemon=True)
        worker_thread.start()

        # Run poller in main thread
        poller = MeetingPoller(config)
        poller.run_loop(interval_minutes=interval)
    else:
        click.echo("🔍 Running single discovery cycle")
        click.echo("")

        poller = MeetingPoller(config)
        stats = poller.run_discovery(dry_run=dry_run)

        click.echo("")
        click.echo("📊 Results:")
        click.echo(f"   Discovered: {stats['discovered']}")
        click.echo(f"   New: {stats['new']}")
        click.echo(f"   Queued: {stats['queued']}")
        click.echo(f"   Skipped: {stats['skipped']}")
        click.echo(f"   Errors: {stats['errors']}")
        click.echo("")


@cli.command()
@click.option("--port", type=int, default=8000, help="Port to run on")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
def serve(port, host, reload):
    """Start web dashboard.

    Example:
        python -m src.main serve                  # Start on port 8000
        python -m src.main serve --port 8080      # Custom port
        python -m src.main serve --reload         # Auto-reload on code changes
    """
    from src.web.app import run_server

    click.echo(f"🌐 Starting web dashboard on http://{host}:{port}")
    click.echo("   Press Ctrl+C to stop")
    click.echo("")

    run_server(host=host, port=port, reload=reload)


@cli.command()
def start_all():
    """Start both poller and web dashboard (development mode).

    Example:
        python -m src.main start-all
    """
    import subprocess
    import os

    click.echo("🚀 Starting all services in development mode...")
    click.echo("")

    # This is a convenience command for development
    # In production, use systemd services instead
    click.echo("⚠️  This is for development only!")
    click.echo("   For production, use: ./deployment/setup-services.sh")
    click.echo("")

    try:
        # Start web server
        web_proc = subprocess.Popen(
            ["python", "-m", "src.main", "serve"],
            cwd=os.getcwd()
        )

        # Start poller + worker
        poller_proc = subprocess.Popen(
            ["python", "-m", "src.main", "run", "--loop"],
            cwd=os.getcwd()
        )

        click.echo("✅ Services started:")
        click.echo(f"   Web dashboard: PID {web_proc.pid}")
        click.echo(f"   Poller/Worker: PID {poller_proc.pid}")
        click.echo("")
        click.echo("   Web: http://localhost:8000")
        click.echo("   Press Ctrl+C to stop all services")
        click.echo("")

        # Wait for processes
        web_proc.wait()
        poller_proc.wait()

    except KeyboardInterrupt:
        click.echo("\n\n🛑 Stopping services...")
        web_proc.terminate()
        poller_proc.terminate()
        web_proc.wait()
        poller_proc.wait()
        click.echo("✅ All services stopped")


# ============================================================================
# ENTRY POINT
# ============================================================================


if __name__ == "__main__":
    cli(obj={})
