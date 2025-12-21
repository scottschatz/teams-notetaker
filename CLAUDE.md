# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Teams Meeting Transcript Summarizer: Discovers Microsoft Teams meetings via webhooks, fetches transcripts, generates AI summaries using Claude, and distributes via email. Runs as systemd services on WSL2.

## Common Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Run services (production)
systemctl --user start teams-notetaker-poller   # Backfill + worker (webhook-driven)
systemctl --user start teams-notetaker-web      # Web dashboard on port 8000

# View logs
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f

# CLI commands
python -m src.main run --loop          # Backfill once, then run worker only
python -m src.main run --poll-loop     # Legacy continuous polling mode
python -m src.main serve --port 8000   # Web dashboard
python -m src.main health              # Check API connections
python -m src.main db init             # Initialize database
python -m src.main pilot list          # List pilot users
python -m src.main webhooks status     # Check webhook subscription
python -m src.main webhooks listen     # Start webhook listener

# Database
psql -h localhost -U postgres -d teams_notetaker
```

## Architecture

### Meeting Discovery (Two Paths)
1. **Webhook (Primary)**: Azure Relay receives call record notifications → `CallRecordHandler` creates meeting + jobs
2. **Backfill (Startup only)**: `MeetingPoller.run_discovery()` queries calendars once, worker processes jobs

### Job Processing Pipeline
```
fetch_transcript → generate_summary → distribute
     (Job 1)           (Job 2)          (Job 3)
```
Jobs use `depends_on_job_id` for ordering. Worker claims with `FOR UPDATE SKIP LOCKED`.

### Key Modules
- `src/webhooks/call_records_handler.py` - Webhook notification processing, meeting creation
- `src/jobs/processors/transcript.py` - Fetches VTT from Graph API, parses speakers
- `src/jobs/processors/summary.py` - Calls Claude API for 6-stage extraction
- `src/jobs/processors/distribution.py` - Sends emails via Graph API
- `src/discovery/poller.py` - Calendar-based discovery (backfill only)
- `src/graph/client.py` - MSAL auth, Graph API wrapper
- `src/core/database.py` - SQLAlchemy models (Meeting, Transcript, Summary, JobQueue, etc.)
- `src/web/` - FastAPI web dashboard with Jinja2 templates
- `src/inbox/monitor.py` - Email inbox monitoring for subscribe/unsubscribe commands

## Critical Implementation Details

### Timezone Handling (DO NOT REMOVE THIS SECTION)
- **Database stores UTC-naive datetimes** (values are UTC, no tzinfo)
- **UI and emails display in Eastern Time** (America/New_York)
- Graph API returns UTC with 'Z' suffix
- JavaScript display adds 'Z' back and converts to Eastern
- Use `datetime.utcnow()` or strip timezone: `dt.replace(tzinfo=None)`
- PostgreSQL timezone is set to 'America/New_York' but datetime columns store UTC
- When querying by "10am Eastern", convert to UTC first: 10am EST = 15:00 UTC (winter)

### Async Processing
All blocking I/O in async processors must use `run_in_executor`:
```python
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, lambda: blocking_call(...))
```

### Graph API Sessions
CallRecords don't include sessions by default:
```python
# MUST use $expand or fetch separately
client.get(f"/communications/callRecords/{id}", params={"$expand": "sessions"})
```

### Meeting ID Formats
- Calendar events: `AAMkADhl...` (calendar event ID)
- Online meetings: `MSpmMmNi...` (online meeting ID)
- Transcript processor needs online meeting ID; use `online_meeting_id` in job input for calendar-discovered meetings

### Subscriber System (NOT Pilot Users)
- **Users opt-in by sending email** to note.taker@townsquaremedia.com with subject "subscribe"
- Pilot mode is DEPRECATED - now only filters by subscriber list
- Distribution only sends to users in `meeting_subscribers` table with `is_subscribed = true`
- Inbox monitoring automatically processes subscribe/unsubscribe emails

### Chat Event Signals (Sprint 3)
- System monitors Teams chat for recording/transcript availability events
- Uses chat signals to determine optimal retry timing:
  - **Recording started** → Transcript likely ready in 5-10 min
  - **Transcript available** → Fetch immediately
  - **No signals** → Use conservative 15/30/60 min retry schedule
- Auto-sets `recording_started` and `transcript_available` flags on successful transcript fetch

### Recent Features (December 2025)

#### Column Header Filters
- Meetings table has dropdown filters on: Status, Source, Organizer, Model, Rec/Tsc columns
- Filters are client-side via Alpine.js
- Filter state preserved in URL query params

#### Subscriber Counts & Time Filtering
- Users/Subscribers page shows meeting attendance and summary counts per user
- Time period filter: Last 7 days, Last 30 days, Last 90 days, All time
- Counts updated dynamically based on selected period

#### Download Endpoints
- VTT download: `/meetings/{id}/transcript/download`
- Markdown summary download: `/meetings/{id}/summary/download`
- Both respect user permissions

#### Enhanced Email Distribution
- Can include transcript as attachment (configurable)
- Works for transcript-only meetings (no summary)
- Profile photos for all attendees
- Clickable timestamps to recording
- Compact attendee display (first 5 detailed, rest simplified)

## Configuration

- `.env` - Secrets (Azure credentials, Claude API key, database URL)
- `config.yaml` - Application settings (pilot mode, email config, intervals)
- PostgreSQL timezone is `America/New_York`

## Testing

```bash
# Run tests
pytest tests/

# Run specific test
pytest tests/test_backfill.py -v

# Note: Tests use SQLite, need JSONB compatibility patch
```

## Service Files

Located in `~/.config/systemd/user/`:
- `teams-notetaker-poller.service` - Worker + one-time backfill
- `teams-notetaker-web.service` - FastAPI dashboard
- `teams-notetaker-webhook.service` - Azure Relay listener (optional)

## Web Dashboard Features

### Meetings Page
- Sortable table with pagination
- Column filters (Status, Source, Organizer, Model, Rec/Tsc)
- Download transcript (VTT) and summary (Markdown)
- Manual processing buttons
- Detailed meeting view with all metadata

### Diagnostics Page
- Force backfill (custom hours)
- Backfill history viewer
- Inbox monitoring status
- Send test emails
- Job queue statistics

### Admin Pages
- Users/Subscribers management with counts
- Email alias management
- Subscribe/unsubscribe actions
- Time-filtered statistics

## Development Workflow

### Making Changes
1. Edit code in WSL
2. Clear Python cache: `find . -type d -name __pycache__ -exec rm -rf {} +`
3. Restart services: `systemctl --user restart teams-notetaker-poller teams-notetaker-web`
4. Check logs: `journalctl --user -u teams-notetaker-poller -f`

### Database Changes
1. Update models in `src/core/database.py`
2. Create migration SQL in `migrations/` directory
3. Run migration manually or via `python -m src.main db migrate`

### Adding New Processors
1. Create processor class in `src/jobs/processors/`
2. Inherit from `BaseJobProcessor`
3. Implement `async def process(self, job: JobQueue) -> Dict[str, Any]`
4. Register in `src/jobs/processors/__init__.py`

### Adding Web Routes
1. Create/edit router in `src/web/routers/`
2. Create template in `src/web/templates/`
3. Register router in `src/web/app.py`

## Common Issues & Solutions

### Issue: Jobs stuck in "running" status
**Cause**: Worker crashed, stale heartbeat
**Solution**: Self-healing cleanup after 15 minutes, or manually reset:
```sql
UPDATE job_queue SET status = 'pending', worker_id = NULL
WHERE status = 'running' AND heartbeat_at < NOW() - INTERVAL '15 minutes';
```

### Issue: Transcripts not found
**Cause**: Transcript not yet available (Teams takes 5-60 minutes)
**Solution**: Retry logic waits up to 1hr with adaptive scheduling based on chat signals

### Issue: 403 error accessing organizer's transcripts
**Cause**: Application permissions don't include organizer's transcripts
**Solution**: Fallback to pilot user who attended meeting

### Issue: Email not sent to user
**Cause**: User hasn't subscribed
**Solution**: User must send email to note.taker@townsquaremedia.com with subject "subscribe"

### Issue: Duplicate meetings created
**Cause**: Webhook and backfill both processed same callRecord
**Solution**: ProcessedCallRecord deduplication table

## API Rate Limits & Performance

### Microsoft Graph API
- Limit: ~2000 requests/minute (org-wide)
- Mitigation: Exponential backoff, respects Retry-After
- Improvement: Batch requests where possible

### AI Summarization (Gemini Primary + Haiku Fallback)
- **Primary**: Gemini 3 Flash (`gemini-2.0-flash`) - 48% cheaper
- **Fallback**: Claude Haiku 4.5 - used when Gemini fails
- Cost: ~$0.0025 per meeting (Gemini), ~$0.004 (Haiku)
- Approach tracked in `summaries.approach` column: `gemini_single_call` or `haiku_fallback`
- Prompt files: `src/ai/prompts/gemini_prompt.py` (primary), `single_call_prompt.py` (fallback)
- Requires `GOOGLE_API_KEY` env var; if missing, uses Haiku only

### Database Connection Pool
- Pool: 10 base + 20 overflow
- Risk: Connection exhaustion with >30 concurrent operations
- Mitigation: Session auto-close in processors

## Security Considerations

### Credential Management
- Secrets in .env (never committed)
- File permissions: 600 (owner read/write only)
- MSAL token caching (1-hour TTL)
- JWT tokens for web dashboard

### PII Handling
- Emails stored in database
- Transcript content stored for AI processing
- SharePoint URLs respect Microsoft 365 permissions

### Vulnerabilities
- SQL Injection: Mitigated via SQLAlchemy ORM
- XSS: Email clients handle sanitization
- Webhook Validation: Missing signature verification (TODO)

## Monitoring & Debugging

### Health Checks
```bash
python -m src.main health
curl http://localhost:8000/health
```

### Queue Statistics
```bash
curl http://localhost:8000/api/stats
```

### Log Monitoring
```bash
# Follow poller logs
journalctl --user -u teams-notetaker-poller -f

# Filter for errors
journalctl --user -u teams-notetaker-poller | grep ERROR

# Show last 100 lines
journalctl --user -u teams-notetaker-poller -n 100
```

### Database Queries
```sql
-- Check recent jobs
SELECT job_type, status, COUNT(*) FROM job_queue
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY job_type, status;

-- Check subscriber count
SELECT COUNT(*) FROM meeting_subscribers WHERE is_subscribed = true;

-- Find meetings without summaries
SELECT id, subject, start_time FROM meetings
WHERE status = 'completed' AND has_summary = false
ORDER BY start_time DESC LIMIT 10;
```

## Best Practices

### Code Quality
- Type hints throughout
- Comprehensive docstrings
- Error handling at all levels
- Logging with appropriate levels

### Database Operations
- Use context managers for sessions
- Parameterized queries only
- Index on frequently queried columns
- Clean up old data (>90 days)

### API Calls
- Exponential backoff for retries
- Respect rate limits
- Cache where appropriate
- Use batch operations when available

### Testing
- Test fixtures for Graph API responses
- Mock external API calls
- Test edge cases (empty results, errors)
- SQLite compatibility for tests

## Future Enhancements

### Short-Term
- Webhook signature validation
- Prometheus metrics endpoint
- Log rotation configuration
- Database archival (>90 days)

### Medium-Term
- Multi-worker support with load balancing
- Cost optimization with prompt caching
- Self-service opt-in/opt-out via web dashboard
- Custom summary templates

### Long-Term
- Microsoft Teams app with bot
- Analytics dashboard
- Multi-tenant support
- Slack/Jira integrations

## References

- [Microsoft Graph API Documentation](https://learn.microsoft.com/en-us/graph/)
- [Claude API Documentation](https://docs.anthropic.com/)
- [Azure Relay Hybrid Connections](https://learn.microsoft.com/en-us/azure/azure-relay/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)

---

**Last Updated**: 2025-12-19
**Status**: Production Ready
