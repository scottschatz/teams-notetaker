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
- `src/jobs/processors/transcript.py` - Fetches VTT from Graph API, parses speakers, marks speakers as attended
- `src/jobs/processors/summary.py` - Calls Claude API using single-call comprehensive prompt
- `src/jobs/processors/distribution.py` - Sends emails via Graph API
- `src/discovery/poller.py` - Calendar-based discovery (backfill only)
- `src/graph/client.py` - MSAL auth, Graph API wrapper
- `src/core/database.py` - SQLAlchemy models (Meeting, Transcript, Summary, JobQueue, etc.)
- `src/ai/prompts/single_call_prompt.py` - Sophisticated single-call summarization prompt
- `src/ai/summarizer.py` - SingleCallSummarizer with Haiku primary, Gemini fallback
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
- Calendar events: `AAMkADhl...` (calendar event ID) - stored in `calendar_event_id`
- Online meetings: `MSpmMmNi...` (online meeting ID) - stored in `online_meeting_id`
- Call records: Graph API callRecord ID - stored in `call_record_id`
- Chat ID: `19:...@thread.v2` format - extracted from `join_url`, stored in `chat_id`
- Transcript processor needs online meeting ID; use `online_meeting_id` in job input for calendar-discovered meetings

### Single-Call Prompt Architecture (CRITICAL FOR AI QUALITY)

The system uses a sophisticated single-call prompt (`src/ai/prompts/single_call_prompt.py`) that extracts all structured data in one API call. This section documents the prompt's key innovations:

#### Audience Inference & Adaptation
- Prompt automatically infers meeting audience: TECHNICAL, OPERATIONAL, or BUSINESS
- Inference based on: technical detail level > decisions made > participant titles (priority order)
- **CRITICAL**: Audience affects explanation depth ONLY, not detail removal
- TECHNICAL: Minimal background explanation, assumes domain knowledge
- OPERATIONAL: Explain tools/processes where needed for coordination
- BUSINESS: Briefly explain technical concepts on first mention
- **Audience does NOT mean sanitize content** - all technical detail, numbers, tools preserved

#### Internal Classification (Not Output)
Before generating summary, prompt determines:
- `inferred_audience`: technical | operational | business
- `meeting_complexity`: low | medium | high (based on CONTENT, not duration)
  - LOW: Single-topic sync, status update, brief check-in
  - MEDIUM: Multi-topic discussion, some decisions, moderate detail
  - HIGH: Strategic decisions, financial analysis, multi-stakeholder, technical architecture
- Used to adjust explanation depth and `discussion_notes` length

#### Critical Preservation Rules (8 Rules)
These ensure NO detail loss across all sections:
1. **Entity Preservation**: Person + number associations MUST appear (e.g., "Erica has 6 projects" → key_numbers)
2. **Action Item Granularity**: NEVER combine action items for multiple people - one per person
3. **Numeric Completeness**: Every significant number (costs, counts, percentages, timeframes) in key_numbers
4. **Thematic Exhaustiveness**: Discussion notes cover ALL distinct topics - no merging for brevity
5. **Section Independence**: `ai_answerable_questions` is BONUS, doesn't reduce other sections
6. **Entity Anchoring**: Important proper nouns/numbers appear in TWO sections (structured + discussion_notes)
7. **Decision Justification**: Every decision includes technical/business WHY, not just WHAT
8. **Cross-Reference Coherence**: Every detail in executive_summary is explained in discussion_notes

#### Collapsible Call Notes (UI Display Feature)
- Generated alongside `discussion_notes` for collapsible UI display (Teams, dashboards)
- 30-50% length of `discussion_notes`
- Same thematic subheadings, preserves decisions/numbers/action-driving context
- Removes narrative elaboration and redundant explanation
- Must stand alone if expanded independently
- **Prefer short paragraphs over bullets** - bullets only for 3+ parallel items
- Example use case: Teams adaptive card "Show More" expansion

#### Quality Flags for Escalation (Optional)
Prompt can output `quality_flags` field for programmatic escalation:
```json
{
  "confidence_level": "high" | "medium" | "low",
  "potential_detail_loss": true | false,
  "reason": "Brief explanation if low confidence or potential loss"
}
```
- Include ONLY when: transcript fragmented, speakers unclear, high complexity compressed
- Enables automatic re-summarization with Sonnet 4.5 for quality assurance
- **Haiku primary (~$0.06) → Sonnet escalation (~$0.24) on quality concerns**

#### RAG Metadata Extraction (Enterprise Intelligence)
Prompt extracts rich metadata for future knowledge base and chatbot:
- **technical_entities**: Tools, technologies, services, ports, protocols (e.g., Nginx, FastAPI, Port 443)
- **projects_referenced**: Project names, repo names, internal tools with owners and status
- **rejected_alternatives**: Options considered but NOT chosen (prevents re-litigating decisions)
- **risk_indicators**: Sentiment, urgency, blockers, customer issues, deadline pressure
- **knowledge_graph_links**: Relationship triples (subject-predicate-object)
  - Example: `{"subject": "Erica Anderson", "predicate": "owner_of", "object": "TLA Upload Tool"}`
  - Enables queries: "Who owns Nginx?" or "What projects is Erica working on?"

#### Meeting Complexity is Content-Based, Not Duration-Based
- **OLD ASSUMPTION (WRONG)**: Long meetings = complex, short meetings = simple
- **CORRECT**: Complexity determined by content richness, not duration
  - 15-minute strategic budget decision = HIGH complexity
  - 60-minute status update check-in = LOW complexity
- Prompt determines complexity before generating summary
- Affects `discussion_notes` length and explanation depth

#### Discussion Notes Length Guidance (by Audience + Complexity)
Prompt specifies exact word count targets:
- **LOW complexity**: 200-300 words (any audience)
- **MEDIUM complexity**:
  - Technical: 350-500 words
  - Operational: 300-450 words
  - Business: 250-400 words
- **HIGH complexity**:
  - Technical: 500-800 words
  - Operational: 400-650 words
  - Business: 350-600 words
- **Failure to meet appropriate depth = INVALID output**

#### Participant Name Spelling Correction
- Prompt receives list of correct participant names from meeting invite
- Corrects phonetic misspellings in transcript (e.g., "Eric" → "Erik Hellum")
- Includes hardcoded company executive list with correct spellings
- Common corrections: "half power" → "half-hour", "Lisa Durata" → "Lisa Daretta"
- **All participant names bolded in output using markdown `**Name**`**

#### Structured Output Fields
```json
{
  "action_items": [...],           // With category: immediate/follow_up/sop
  "decisions": [...],               // With rationale_one_line, reasoning, impact
  "highlights": [...],              // 5-8 max, prioritized by importance
  "key_numbers": [...],             // All financial/quantitative metrics (max 20)
  "executive_summary": "...",       // 50-125 words based on complexity
  "discussion_notes": "...",        // Thematic narrative, length by audience+complexity
  "collapsible_call_notes": "...",  // 30-50% of discussion_notes, scannable
  "ai_answerable_questions": [...], // ONLY explicit questions, NO inferred
  // RAG metadata fields omitted for brevity
  "quality_flags": {...}            // Optional, for escalation decisions
}
```

#### Top Speakers Fix (Transcript Processor)
- **Problem**: Call record sessions don't always match who actually spoke
- **Solution**: Transcript processor auto-marks speakers as `attended=true` in `meeting_participants`
- Uses speaker names from parsed VTT transcript (lines 379-395 in `transcript.py`)
- Flexible matching: exact or normalized (dots/underscores → spaces)
- **Impact**: Email "Top Speakers" list now shows correct people who spoke, not just joined

### Subscriber System (Opt-In via Email)
- **Users opt-in by sending email** to note.taker@townsquaremedia.com with subject "subscribe"
- Pilot mode is DEPRECATED - now filters by user preferences
- Distribution only sends to users who have opted-in via email or admin configuration
- Inbox monitoring automatically processes subscribe/unsubscribe emails
- Email aliases supported - multiple emails can map to same user

### Chat Event Signals & Transcript Detection
- System monitors Teams chat for recording/transcript availability events
- Chat ID extraction from `join_url` enables event monitoring: `19:...@thread.v2`
- Uses chat signals to determine optimal retry timing:
  - **Recording started** (`recording_started` flag) → Transcript likely ready in 5-10 min
  - **Transcript available** (`transcript_available` flag) → Fetch immediately
  - **No signals** → Use conservative 15/30/60 min retry schedule
- Auto-sets flags on successful transcript fetch
- **Backfill script**: `/scripts/backfill_chat_id.py` extracts chat_id from existing meetings

### Azure AD User Properties
- Participant metadata enrichment from Azure AD Graph API
- Properties fetched: `job_title`, `department`, `office_location`, `company_name`
- Stored in `meeting_participants` table for each attendee
- **Backfill script**: `/scripts/backfill_azure_ad.py` populates existing participants
- Used for enhanced email formatting and future analytics

### 1:1 Call Filtering
- Meetings can be filtered by call type (`call_type` column)
- Call types: `groupCall`, `peerToPeer` (1:1), `scheduled`, `adHoc`, `unknown`
- Web dashboard: `include_one_on_one` parameter excludes peerToPeer calls by default
- Useful for excluding 1:1 conversations from meeting summaries

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

## Backfill Scripts

Located in `/scripts/` directory:

### backfill_chat_id.py
- Extracts `chat_id` from `join_url` for existing meetings
- Enables chat event detection for transcript retry logic
- Parses format: `19:...@thread.v2` from Teams meeting URLs
- Run: `python scripts/backfill_chat_id.py`

### backfill_azure_ad.py
- Fetches Azure AD properties for existing participants
- Populates: `job_title`, `department`, `office_location`, `company_name`
- Deduplicates API calls by email
- Run: `python scripts/backfill_azure_ad.py`

### setup-webhook-service.sh
- Deploys systemd webhook listener service (optional)
- Alternative to embedding webhook in poller service

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

### AI Summarization (Single-Call Architecture)
- **Primary Model**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Cost**: ~$0.06 per meeting (Haiku: $1.00/MTok input, $5.00/MTok output)
- **Alternative**: Gemini 3 Flash available but disabled (`USE_GEMINI_PRIMARY = False` in `src/ai/summarizer.py`)
  - Gemini disabled due to quality issues: duration extraction ("None minutes"), lower detail quality
  - Can be re-enabled by setting `USE_GEMINI_PRIMARY = True` in code
  - Cost if enabled: ~$0.03 per meeting (48% cheaper than Haiku)
- **Approach**: Single-call comprehensive prompt (not multi-stage)
  - One API call extracts all structured data
  - Faster and more cost-effective than 6-stage multi-call
  - Sophisticated prompt with audience inference, entity anchoring, quality flags
- **Model Escalation**: Quality flags enable programmatic escalation to Sonnet for complex meetings
  - Haiku: ~$0.06/meeting (primary)
  - Sonnet 4.5: ~$0.24/meeting (escalation for low confidence or potential detail loss)
- Approach tracked in `summaries.approach` column: `haiku_single_call`, `gemini_single_call`, or legacy
- Prompt file: `src/ai/prompts/single_call_prompt.py`
- `GOOGLE_API_KEY` env var optional; if missing, uses Haiku only

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

**Last Updated**: 2025-12-23
**Status**: Production Ready

## Recent Documentation Updates (2025-12-23)

### AI Summarization Architecture
- Documented single-call prompt architecture with audience inference
- Clarified Haiku is primary model (~$0.06/meeting), Gemini available but disabled
- Documented quality flags for programmatic escalation to Sonnet (~$0.24/meeting)
- Meeting complexity is content-based, not duration-based
- Added comprehensive prompt structure documentation:
  - Audience Inference & Adaptation (Technical/Operational/Business)
  - Critical Preservation Rules (8 rules ensuring no detail loss)
  - Entity Anchoring concept (proper nouns appear in 2+ sections)
  - Collapsible Call Notes (30-50% of discussion_notes for UI display)
  - RAG Metadata extraction (technical_entities, knowledge_graph_links, etc.)
  - Discussion Notes length guidance by audience + complexity matrix

### Transcript Processor Fix
- Documented auto-marking of transcript speakers as attended=true in meeting_participants
- Fixes "Top Speakers" list in emails showing wrong people
- Uses flexible name matching (exact or normalized with dots/underscores → spaces)
- Implementation: lines 379-395 in `src/jobs/processors/transcript.py`
