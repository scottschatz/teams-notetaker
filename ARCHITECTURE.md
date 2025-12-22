# Teams Meeting Transcript Summarizer - System Architecture

**Version**: 3.0 (Webhook-driven with subscriber system)
**Last Updated**: 2025-12-22
**Status**: Production

## Recent Updates (December 2025)
- **Column Header Filters**: Dropdown filters on Status, Source, Organizer, Model, Rec/Tsc columns
- **Subscriber Counts**: Meeting attendance and summary counts per user with time filtering
- **Download Endpoints**: VTT transcript and Markdown summary downloads
- **Chat Event Detection**: Auto-detection of recording/transcript availability from Teams chat
- **Chat ID Extraction**: Automated extraction from join URLs for event monitoring
- **Azure AD Properties**: Participant job title, department, office location, company name
- **1:1 Call Filtering**: Filter by call type (exclude peerToPeer calls)
- **Enhanced Emails**: Optional transcript attachment, improved formatting
- **Inbox Monitoring**: Automated subscribe/unsubscribe email processing
- **Backfill Scripts**: Tools for chat_id and Azure AD property backfilling

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Component Overview](#component-overview)
4. [Data Flow](#data-flow)
5. [Database Schema](#database-schema)
6. [Job Processing Pipeline](#job-processing-pipeline)
7. [Configuration](#configuration)
8. [Deployment Architecture](#deployment-architecture)
9. [Security Considerations](#security-considerations)
10. [Performance & Scalability](#performance--scalability)

---

## System Overview

The Teams Meeting Transcript Summarizer is an enterprise-grade system that automatically:

1. **Discovers** Microsoft Teams meetings with transcripts (webhook-driven + backfill)
2. **Fetches** VTT transcripts from Microsoft Graph API
3. **Generates** AI-powered structured summaries using Claude Haiku 4.5
4. **Distributes** summaries via email with rich formatting and profile photos to subscribers
5. **Monitors** email inbox for subscribe/unsubscribe requests
6. **Detects** chat events for optimal transcript retry timing

### Key Design Principles

- **Webhook-First**: Real-time meeting discovery via Azure Relay webhooks (org-wide)
- **Subscriber System**: Users opt-in by email, inbox monitoring processes requests automatically
- **Async Processing**: Job queue with concurrent workers (5-10 jobs in parallel)
- **Idempotency**: Duplicate-safe with deduplication at multiple levels
- **Self-Healing**: Automatic recovery of stale/orphaned jobs
- **Graceful Degradation**: Retries with exponential backoff, continues on partial failures

---

## Architecture Diagram

### High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         MICROSOFT TEAMS ECOSYSTEM                         │
│  - callRecords (all meetings)                                            │
│  - callTranscripts (transcript-ready notifications)                       │
│  - onlineMeetings (metadata, participants)                               │
│  - chats (user commands)                                                  │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                    ┌───────────┴────────────┐
                    │                        │
            Webhooks (Azure Relay)    Calendar Backfill (Startup)
                    │                        │
                    └───────────┬────────────┘
                                │
                                v
                ┌───────────────────────────────┐
                │   CallRecordsWebhookHandler   │
                │  - Validates notifications     │
                │  - Checks for opted-in users   │
                │  - Deduplicates callRecords    │
                │  - Creates meetings + jobs     │
                └───────────┬───────────────────┘
                            │
                            v
                ┌───────────────────────────────┐
                │       PostgreSQL Database      │
                │  - Meetings, Transcripts       │
                │  - Summaries, Distributions    │
                │  - Job Queue (SKIP LOCKED)     │
                │  - User Preferences            │
                │  - Deduplication tracking      │
                └───────────┬───────────────────┘
                            │
                            v
            ┌───────────────────────────────────────┐
            │          Async Job Worker              │
            │  - Claims jobs (FOR UPDATE SKIP LOCKED)│
            │  - Concurrent processing (5 jobs)      │
            │  - Heartbeat updates (30s)             │
            │  - Exponential backoff retries         │
            │  - Self-healing (60s cleanup)          │
            └───────────┬───────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        v               v               v
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Fetch    │  │ Generate │  │ Distrib- │
  │ Transcript  │ Summary  │  │ ute      │
  │ Processor│  │ Processor│  │ Processor│
  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │             │              │
       v             v              v
  Graph API    Claude API      Graph API
  (getAllTranscripts) (Sonnet 4.5)  (sendMail)
```

### Webhook Discovery Flow (Primary Path)

```
Microsoft Teams Call Ends
        │
        v
callRecords webhook → Azure Relay → CallRecordsWebhookHandler
        │                                   │
        v                                   v
   Extract participants          Check ProcessedCallRecord
        │                          (deduplication)
        v                                   │
   Graph API lookup                         v
   (user emails)                   Check for opted-in users
        │                                   │
        v                                   v
   Filter by preferences            Create Meeting record
        │                                   │
        v                                   v
   Meeting has opted-in user?      Enqueue fetch_transcript job
        │                                   │
        ├─ NO → Mark processed              v
        │       (skipped)              Job Worker picks up
        │
        └─ YES → Continue to job queue
```

### Backfill Flow (Gap Detection)

```
System Startup
        │
        v
   Find last webhook timestamp
        │
        v
   Calculate gap (time since last webhook)
        │
        v
   Query callRecords since gap_time - 5min
   (uses pagination for >60 results)
        │
        v
   For each callRecord:
     - Check ProcessedCallRecord (skip if exists)
     - Extract participants via Graph API
     - Check for opted-in users
     - Create meeting + jobs if opted-in users found
        │
        v
   Processing continues via job queue
```

### Job Processing Chain

```
Meeting Created
        │
        v
┌───────────────────────────────────────────────────┐
│ Job 1: fetch_transcript                           │
│  - Find transcript (getAllTranscripts API)        │
│  - Download VTT content                           │
│  - Parse speakers, timestamps                     │
│  - Get SharePoint URLs (transcript + recording)   │
│  - Store in database                              │
│  - Check chat for preference commands             │
│  - Create Job 2 (generate_summary)                │
└───────────────┬───────────────────────────────────┘
                │
                v
┌───────────────────────────────────────────────────┐
│ Job 2: generate_summary                           │
│  - Format transcript for Claude                   │
│  - Call Claude API (single-call or multi-stage)   │
│  - Extract structured data:                       │
│    * Action items with assignees                  │
│    * Key decisions with impact                    │
│    * Topic segments with timestamps               │
│    * Key moments (highlights)                     │
│    * Person mentions                              │
│    * Key numbers (financial/quantitative)         │
│  - Store summary in database                      │
│  - Job 3 already created by initial chain         │
└───────────────┬───────────────────────────────────┘
                │
                v
┌───────────────────────────────────────────────────┐
│ Job 3: distribute                                 │
│  - Filter recipients by preferences               │
│  - Post to Teams chat (chat-first)                │
│  - Send email with enhanced formatting            │
│  - Track distribution records                     │
│  - Mark meeting as completed                      │
└───────────────────────────────────────────────────┘
```

---

## Component Overview

### Core Components

#### 1. CallRecordsWebhookHandler (`src/webhooks/call_records_handler.py`)
- **Purpose**: Process Microsoft Graph webhook notifications
- **Responsibilities**:
  - Handle callRecords notifications (all meetings)
  - Handle callTranscripts notifications (transcript-ready events)
  - Validate notifications, extract metadata
  - Check for opted-in participants
  - Deduplicate using ProcessedCallRecord table
  - Create Meeting records and enqueue jobs
  - Backfill recent meetings using gap detection

**Key Methods**:
- `handle_notification()`: Main entry point for webhooks
- `_process_call_record()`: Process individual callRecord
- `_extract_participants()`: Extract participants from sessions (includes PSTN, guests)
- `backfill_recent_meetings()`: Gap-based backfill with pagination

#### 2. Job Worker (`src/jobs/worker.py`)
- **Purpose**: Async job processing engine
- **Responsibilities**:
  - Claim jobs atomically using `FOR UPDATE SKIP LOCKED`
  - Execute processors concurrently (5 jobs in parallel)
  - Update heartbeats every 30 seconds
  - Handle timeouts, retries, failures
  - Self-healing cleanup of stale/orphaned jobs

**Key Features**:
- Graceful shutdown (waits up to 30s for active jobs)
- Exponential backoff for retries
- Worker ID tracking for distributed processing
- Cleanup interval: 60 seconds

#### 3. Job Processors

##### TranscriptProcessor (`src/jobs/processors/transcript.py`)
- Fetch transcript using getAllTranscripts API (beta endpoint)
- Parse VTT format (speakers, timestamps, text)
- Extract SharePoint URLs for transcript and recording
- Check Teams chat for preference commands
- Retry logic: 15min, 30min, 60min (max 3 retries)

##### SummaryProcessor (`src/jobs/processors/summary.py`)
- Format transcript for Claude API
- Two modes:
  - **Single-call** (default): One API call, faster, cheaper
  - **Multi-stage**: 6 API calls, more structured
- Extract:
  - Executive summary
  - Action items (assignee, deadline, context)
  - Decisions (reasoning, impact, participants)
  - Topics (segments with duration, speakers)
  - Highlights (key moments with timestamps)
  - Mentions (person references)
  - Key numbers (financial/quantitative metrics)
- Convert markdown to HTML
- Store versioned summaries

##### DistributionProcessor (`src/jobs/processors/distribution.py`)
- Filter recipients by user preferences (opt-in/opt-out)
- Post to Teams chat (chat-first strategy)
- Send email with:
  - Rich HTML formatting
  - Profile photos (48x48 circular)
  - Clickable timestamps
  - SharePoint links (respects permissions)
  - Compact attendee display (first 5 detailed, rest simplified)
  - Invitees section (invited but didn't attend)
- Track distribution records

#### 4. Graph API Client (`src/graph/client.py`)
- **Purpose**: Authenticated access to Microsoft Graph API
- **Features**:
  - MSAL authentication with token caching
  - Auto-refresh on 401 errors
  - Rate limit handling (429 with Retry-After)
  - Exponential backoff for 5xx errors
  - Pagination support (`@odata.nextLink`)
  - Support for both v1.0 and beta endpoints

**Key Methods**:
- `get()`, `post()`, `patch()`, `delete()`: HTTP methods
- `get_paged()`: Automatic pagination
- `enrich_user_with_photo_and_title()`: Fetch user details + photo

#### 5. Database Manager (`src/core/database.py`)
- **Purpose**: SQLAlchemy ORM and database operations
- **Features**:
  - Connection pooling (10 base, 20 overflow)
  - Session management with context managers
  - Atomic job claiming using raw SQL
  - Relationship management with cascades

**Key Tables** (see Database Schema section)

#### 6. Preference Manager (`src/preferences/user_preferences.py`)
- **Purpose**: User opt-in/opt-out preference management
- **Features**:
  - Global preferences (all meetings)
  - Per-meeting preferences (override global)
  - Priority: Per-meeting > Global > Default (False = opt-out by default)
  - Command processing from Teams chat

#### 7. Web Dashboard (`src/web/app.py`)
- **Purpose**: FastAPI web interface for monitoring and management
- **Features**:
  - Real-time job queue status
  - Meeting and summary browsing
  - Pilot user management
  - Health check endpoints
  - Azure AD SSO + password authentication

---

## Data Flow

### 1. Webhook Notification Path (Real-time)

```
Microsoft Graph sends webhook
    ↓
Azure Relay receives HTTPS POST
    ↓
AzureRelayListener websocket receives notification
    ↓
CallRecordsWebhookHandler.handle_notification()
    ↓
Check ProcessedCallRecord (deduplication)
    ↓
Fetch callRecord with sessions ($expand)
    ↓
Extract participants (Graph API lookups for emails)
    ↓
Check for opted-in users (PreferenceManager)
    ↓ (if opted-in users exist)
Create Meeting record
    ↓
Add participants (MeetingParticipant table)
    ↓
Mark as processed (ProcessedCallRecord)
    ↓
Enqueue fetch_transcript job
    ↓
Worker picks up job (FOR UPDATE SKIP LOCKED)
    ↓
(continues to job processing pipeline)
```

### 2. Backfill Path (Startup)

```
System starts
    ↓
Find last webhook timestamp
    ↓
Calculate gap (or use max lookback)
    ↓
Query callRecords API since gap_time - 5min
    ↓
Handle pagination (@odata.nextLink)
    ↓
For each callRecord:
  - Check ProcessedCallRecord
  - Skip if already processed
  - Fetch sessions separately
  - Extract participants
  - Check for opted-in users
  - Create meeting if opted-in users found
    ↓
Processing continues via job queue
```

### 3. Transcript Fetch Flow

```
fetch_transcript job claimed
    ↓
Get Meeting from database
    ↓
Use organizer_user_id for getAllTranscripts API
    ↓
If transcript_id provided (webhook):
  - Use it directly (reliable)
Otherwise:
  - Search by time (±30 min tolerance)
  - Handle 403 errors (try pilot user fallback)
    ↓
Download VTT content
    ↓
Get SharePoint URLs (transcript + recording)
    ↓
Parse VTT (speakers, timestamps, segments)
    ↓
Extract transcript stats (word count, speaker breakdown)
    ↓
Store Transcript record
    ↓
Check Teams chat for preference commands
  - Parse commands (opt-in, opt-out, disable distribution)
  - Update preferences immediately (inline)
  - Mark messages as processed
    ↓
Create generate_summary job
```

### 4. Summary Generation Flow

```
generate_summary job claimed
    ↓
Get Meeting, Transcript, Participants
    ↓
Format transcript for Claude
    ↓
Call Claude API (single-call or multi-stage)
  - Single-call: 1 API call, faster, cheaper
  - Multi-stage: 6 API calls, more structured
    ↓
Extract structured data:
  - Action items (assignee, deadline, context, timestamp)
  - Decisions (decision, participants, reasoning, impact)
  - Topics (topic, duration, speakers, summary)
  - Highlights (title, timestamp, why_important)
  - Mentions (person, mentioned_by, context, timestamp)
  - Key numbers (value, unit, context, magnitude)
    ↓
Convert markdown to HTML
    ↓
Store Summary record (version tracked)
    ↓
If re-summarization:
  - Mark previous version as superseded
```

### 5. Distribution Flow

```
distribute job claimed
    ↓
Get Meeting, Summary, Participants, Transcript
    ↓
Filter recipients by preferences:
  - Per-meeting preference (highest priority)
  - Global preference (fallback)
  - Default opt-out (system default)
    ↓
POST TO TEAMS CHAT FIRST (chat-first strategy):
  - Format adaptive card with summary
  - Include action items, decisions, highlights
  - Post to meeting chat thread
  - Store Distribution record
    ↓
THEN SEND EMAIL:
  - Enrich participants with photos, job titles
  - Fetch meeting invitees (didn't attend)
  - Format HTML email with:
    * Profile photos (48x48 circular)
    * Bold + blue participant names
    * Clickable timestamps to recording
    * SharePoint links (transcript, recording)
    * Compact attendee list (first 5 detailed)
    * Invitees section
  - Send via Graph API (sendMail)
  - Store Distribution records
    ↓
Update Meeting status = 'completed'
```

---

## Database Schema

### Core Tables

#### meetings
Primary meeting records with metadata and processing status.

**Key Fields**:
- `meeting_id` (unique): Graph API online meeting ID or join URL (legacy)
- `online_meeting_id`: MSp... format, required for transcript API
- `calendar_event_id`: AAMk... format, from calendar discovery
- `call_record_id`: From callRecords webhook
- `subject`, `organizer_email`, `organizer_name`, `organizer_user_id`
- `start_time`, `end_time`, `duration_minutes`
- `join_url`: Teams meeting join URL
- `chat_id`: Teams chat thread ID (19:...@thread.v2 format, extracted from join_url)
- `recording_sharepoint_url`: SharePoint URL for recording
- `status`: discovered, queued, processing, completed, failed, skipped, transcript_only, no_transcript
- `has_transcript`, `has_summary`, `has_distribution`: Processing flags
- `distribution_enabled`: Organizer can disable distribution
- `last_chat_check`: When we last checked chat for commands
- `recording_started`: Boolean flag from chat events
- `transcript_available`: Boolean flag from chat events
- `call_type`: groupCall, peerToPeer (1:1), scheduled, adHoc, unknown

**Indexes**:
- `meeting_id` (unique)
- `organizer_email`
- `start_time`
- `status`

#### meeting_participants
Attendees and invitees for each meeting.

**Key Fields**:
- `meeting_id` (FK to meetings)
- `email`, `display_name`, `role`
- `attended`: True if joined call, False if invited but didn't attend
- `participant_type`: internal, pstn, guest, external
- `is_pilot_user`: Cached pilot status
- `job_title`: Azure AD job title
- `department`: Azure AD department
- `office_location`: Azure AD office location
- `company_name`: Azure AD company name

**Purpose**:
- Distribution recipient list
- Correct name spelling in summaries
- Distinguish attendees from invitees

#### job_queue
Async job queue with dependency management.

**Key Fields**:
- `job_type`: fetch_transcript, generate_summary, distribute
- `meeting_id` (FK to meetings)
- `status`: pending, running, completed, failed, retrying
- `priority`: 1-10 (1 = highest)
- `depends_on_job_id`: Job dependency (FK to job_queue)
- `input_data`, `output_data`: JSONB
- `retry_count`, `max_retries`, `next_retry_at`
- `worker_id`, `heartbeat_at`: Worker tracking

**Indexes**:
- `status, priority DESC, created_at` (job claiming)
- `next_retry_at` (retry scheduling)

**Job Claiming**: Uses `FOR UPDATE SKIP LOCKED` for lock-free concurrency

#### transcripts
VTT transcript content and parsed data.

**Key Fields**:
- `meeting_id` (unique FK to meetings)
- `vtt_content`: Full VTT file content
- `vtt_url`: Graph API URL (may expire)
- `transcript_sharepoint_url`: SharePoint URL (respects permissions)
- `parsed_content`: JSONB array of {speaker, timestamp, text}
- `speaker_count`, `word_count`

#### summaries
AI-generated meeting summaries with structured extractions.

**Key Fields**:
- `meeting_id` (FK to meetings)
- `transcript_id` (FK to transcripts)
- `summary_text`: Markdown summary
- `summary_html`: HTML version
- `action_items_json`: [{description, assignee, deadline, context, timestamp}]
- `decisions_json`: [{decision, participants, reasoning, impact, timestamp}]
- `topics_json`: [{topic, duration, speakers, summary, key_points}]
- `highlights_json`: [{title, timestamp, why_important, type}]
- `mentions_json`: [{person, mentioned_by, context, timestamp, type}]
- `key_numbers_json`: [{value, unit, context, magnitude}]
- `version`: Summary version number (1, 2, 3...)
- `custom_instructions`: User-provided instructions for re-summarization
- `superseded_by`: FK to newer version
- `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`

**Indexes**:
- `meeting_id, version` (version lookup)

#### distributions
Email and Teams chat distribution tracking.

**Key Fields**:
- `meeting_id` (FK to meetings)
- `summary_id` (FK to summaries)
- `distribution_type`: email, teams_chat
- `recipient`: Email or chat ID
- `status`: pending, sent, failed, retrying
- `message_id`: Graph API message ID
- `sent_at`

### Preference Tables (Opt-In System)

#### user_preferences
Global user email preferences.

**Key Fields**:
- `user_email` (PK)
- `receive_emails`: True/False
- `email_preference`: all, opt_in, disabled
- `updated_at`, `updated_by`

#### meeting_preferences
Per-meeting preferences (override global).

**Key Fields**:
- `meeting_id` (FK to meetings)
- `user_email`
- `receive_emails`: True/False
- `updated_by`: user, organizer, system

**Unique**: `(meeting_id, user_email)`

**Priority**: Per-meeting > Global > Default (opt-out)

### Deduplication Tables

#### processed_call_records
Track processed callRecords to prevent duplicates.

**Key Fields**:
- `call_record_id` (PK): Graph API callRecord ID
- `processed_at`
- `source`: webhook, backfill, safety_net

**Purpose**: Prevent duplicate processing from:
- Multiple webhook deliveries
- Webhook + backfill overlap
- Safety net syncs

#### processed_chat_messages
Track processed chat commands.

**Key Fields**:
- `message_id` (PK): Teams message ID
- `chat_id`
- `command_type`: email_me, no_emails, etc.
- `processed_at`
- `result`

**Purpose**: Prevent duplicate command processing

### Supporting Tables

#### pilot_users
Users in pilot program (deprecated - now uses opt-in).

#### app_config
Runtime configuration (editable via dashboard).

#### user_sessions
Web dashboard authentication sessions.

#### auth_flows
OAuth flow tracking (10-minute expiration).

#### system_health_checks
System health monitoring logs.

#### backfill_runs
Backfill operation tracking and statistics.

---

## Job Processing Pipeline

### Job Queue Architecture

**Design Pattern**: Producer-consumer with atomic job claiming

**Key Features**:
1. **Lock-Free Concurrency**: `FOR UPDATE SKIP LOCKED`
2. **Job Dependencies**: `depends_on_job_id` ensures ordering
3. **Priority-Based**: Higher priority jobs processed first
4. **Exponential Backoff**: Retries with increasing delays
5. **Self-Healing**: Automatic recovery of stale/orphaned jobs

### Job Claiming Algorithm

```sql
UPDATE job_queue
SET status = 'running',
    worker_id = :worker_id,
    started_at = NOW(),
    heartbeat_at = NOW()
WHERE id = (
    SELECT id FROM job_queue
    WHERE status IN ('pending', 'retrying')
      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
      AND (depends_on_job_id IS NULL OR
           depends_on_job_id IN (SELECT id FROM job_queue WHERE status = 'completed'))
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING id;
```

**Guarantees**:
- No two workers can claim the same job
- Jobs with failed dependencies are skipped
- Respects retry schedules
- Priority and FIFO ordering

### Job Lifecycle

```
PENDING → (worker claims) → RUNNING → (success) → COMPLETED
    ↑                            │
    │                            │ (failure)
    │                            ↓
    └────────────────────── RETRYING
                                 │
                                 │ (max retries)
                                 ↓
                              FAILED
```

### Retry Strategy

**Transcript Fetching**:
- Retries: 3 (15min, 30min, 60min)
- Total max wait: 1hr 45min
- Reason: Transcripts appear gradually after meeting

**Summary Generation**:
- Retries: 3 (immediate with exponential backoff)
- Delays: 2^retry_count seconds
- Reason: Transient API errors

**Distribution**:
- Retries: 3 (immediate with exponential backoff)
- Delays: 2^retry_count seconds
- Reason: Network issues, rate limits

### Self-Healing Mechanisms

**Stale Job Recovery** (every 60s):
- Find jobs with `status='running'` and `heartbeat_at < 15 minutes ago`
- Reset to `status='retrying'`
- Increment `retry_count`
- Clear `worker_id`

**Orphaned Job Cleanup** (every 60s):
- Find jobs with `status='pending'` and `depends_on_job_id` pointing to failed/orphaned job
- Mark as `status='failed'` with reason "Dependency failed"

**Heartbeat Updates** (every 30s):
- Worker updates `heartbeat_at` for all active jobs
- Proves worker is alive and making progress

---

## Configuration

### Configuration Layers

1. **Secrets** (`.env`):
   - Graph API credentials (client ID, secret, tenant)
   - Claude API key
   - Database credentials
   - Azure AD SSO credentials
   - Azure Relay credentials (webhooks)
   - JWT secret key

2. **Runtime Settings** (`config.yaml`):
   - Polling intervals, lookback hours
   - Pilot mode, max concurrent jobs
   - Email/chat distribution toggles
   - Summarization settings
   - Webhook settings

3. **Database Config** (`app_config` table):
   - Editable via dashboard
   - Overrides config.yaml
   - Not yet implemented

### Key Configuration Options

**Discovery**:
- `webhooks_enabled`: Use webhook-driven discovery (default: true)
- `webhook_backfill_hours`: Max hours to backfill on startup (default: 4)
- `webhook_safety_net_enabled`: Daily catchup for missed meetings (default: true)

**Processing**:
- `max_concurrent_jobs`: Concurrent job processing (default: 5)
- `job_timeout_minutes`: Max time per job (default: 10)
- `worker_heartbeat_interval_seconds`: Heartbeat frequency (default: 30)

**AI Summarization**:
- `use_single_call_summarization`: Single-call vs multi-stage (default: true)
- `summary_max_tokens`: Max Claude API tokens (default: 2000)
- Model in code: claude-haiku-4-5-20251001 (testing, switchable to Sonnet 4.5)

**Distribution**:
- `email_enabled`: Enable email distribution (default: true)
- `email_from`: Sender email address
- `teams_chat_enabled`: Enable Teams chat posting (default: true)
- `debug_mode`: Send only to test recipients (default: false)
- `debug_email_recipients`: Test recipient list

**Preferences**:
- `default_email_preference`: Default opt-in/out (default: false = opt-out)
- `allow_chat_preferences`: Allow chat commands (default: true)

---

## Deployment Architecture

### Systemd Services (WSL2)

#### teams-notetaker-poller.service
- **Purpose**: Backfill once on startup, then run webhook-driven worker
- **Command**: `python -m src.main run --loop`
- **Working Dir**: `/home/sschatz/projects/teams-notetaker`
- **User**: sschatz
- **Restart**: always (10s delay)
- **Limits**:
  - Memory: 1GB
  - CPU: 200%
- **Logs**: `journalctl --user -u teams-notetaker-poller -f`

#### teams-notetaker-web.service
- **Purpose**: FastAPI web dashboard
- **Command**: `python -m src.main serve --port 8000`
- **Working Dir**: `/home/sschatz/projects/teams-notetaker`
- **User**: sschatz
- **Restart**: always (10s delay)
- **Limits**:
  - Memory: 512MB
- **Logs**: `journalctl --user -u teams-notetaker-web -f`

### Auto-Start Configuration

**User Linger** (for unattended operation):
```bash
sudo loginctl enable-linger sschatz
```

**Service Auto-Start**:
```bash
systemctl --user enable teams-notetaker-poller
systemctl --user enable teams-notetaker-web
```

### Logging

**Systemd Journals**:
- Poller: `journalctl --user -u teams-notetaker-poller -f`
- Web: `journalctl --user -u teams-notetaker-web -f`

**File Logs**:
- Location: `logs/worker.log`, `logs/web.log`
- Rotation: Not configured (TODO)
- Format: Timestamp, level, module, message

### Database

**PostgreSQL**:
- Host: localhost (WSL2)
- Port: 5432
- Database: teams_notetaker
- User: postgres
- Timezone: America/New_York (server-level)
- Encoding: UTF-8

**Connection Pooling**:
- Pool size: 10
- Max overflow: 20
- Pre-ping: Enabled (verify connections)

### Azure Services

**Azure Relay** (Hybrid Connections):
- Namespace: `{name}.servicebus.windows.net`
- Hybrid Connection: teams-webhooks
- Purpose: Receive webhooks without public IP
- Protocol: WebSocket (wss://)

**Microsoft Graph API**:
- Endpoint: v1.0 and beta
- Auth: Client credentials flow (MSAL)
- Permissions:
  - CallRecords.Read.All
  - OnlineMeetings.Read.All
  - Chat.Read.All
  - Mail.Send
  - User.Read.All

---

## Security Considerations

### Credential Management

**Secrets in .env**:
- Never committed to git (.gitignore)
- File permissions: 600 (owner read/write only)
- Rotation: Manual (no automation)

**API Keys**:
- Graph API: MSAL token caching (1-hour TTL)
- Claude API: Direct key usage (no caching)
- Azure Relay: Shared Access Key (long-lived)

**JWT Tokens**:
- Web dashboard session tokens
- Secret key in .env (required in production)
- Expiration: Configurable (default: 24 hours)

### Authentication & Authorization

**Web Dashboard**:
- Azure AD SSO (primary)
- Password fallback (development)
- Role-based access: admin, manager, user
- Session management in database

**Graph API**:
- Application permissions (not delegated)
- Service account (no user interaction)
- Token refresh on 401 errors

### Data Protection

**PII Handling**:
- Emails: Stored in database (participants, distributions)
- Names: Stored in database (participants, summaries)
- Transcript Content: Full text stored (for AI processing)
- SharePoint URLs: Respects Microsoft 365 permissions

**Database Security**:
- PostgreSQL authentication required
- No public access (localhost only)
- SSL: Not configured (local-only deployment)

**API Security**:
- HTTPS only (Microsoft Graph, Claude API)
- Rate limiting handled by providers
- Retry logic respects Retry-After headers

### Vulnerabilities & Mitigations

**SQL Injection**:
- Mitigation: SQLAlchemy ORM with parameterized queries
- Raw SQL: Only in job claiming (parameterized)

**XSS (Cross-Site Scripting)**:
- Risk: Email HTML rendering
- Mitigation: Email clients handle sanitization
- Note: No user input in HTML (AI-generated only)

**CSRF**:
- Not applicable: API-based system (no forms)

**Webhook Validation**:
- Missing: No signature verification on webhook notifications
- Risk: Spoofed notifications could create fake meetings
- Recommendation: Implement webhook validation (Graph API doesn't provide signatures, consider IP allowlisting)

**Opt-In Bypass**:
- Risk: User can't prevent meeting creation (only distribution)
- Mitigation: Preferences applied at distribution time
- Note: Meetings/transcripts stored regardless of opt-in status

---

## Performance & Scalability

### Current Capacity

**Job Processing**:
- Concurrent jobs: 5
- Job timeout: 10 minutes
- Throughput: ~30 jobs/hour (assuming 2min avg)

**Transcript Processing**:
- VTT parsing: ~1-2 seconds
- Graph API fetch: ~2-5 seconds
- Total: ~10-15 seconds per meeting

**Summary Generation**:
- Single-call: ~5-15 seconds (2000 words)
- Multi-stage: ~30-60 seconds (6 API calls)
- Cost: ~$0.02-0.04 per meeting (Haiku), ~$0.10-0.15 (Sonnet)

**Email Distribution**:
- Graph API: ~1-3 seconds per email
- Photo enrichment: ~1 second per participant
- Total: ~5-10 seconds per meeting

### Bottlenecks

**1. Claude API Rate Limits**:
- Limit: Depends on tier (not documented in code)
- Mitigation: Exponential backoff, retries
- Improvement: Monitor rate limit headers, queue throttling

**2. Graph API Rate Limits**:
- Limit: ~2000 requests/minute (org-wide)
- Mitigation: Exponential backoff, respects Retry-After
- Improvement: Batch requests where possible

**3. Database Connections**:
- Pool: 10 base + 20 overflow
- Risk: Connection exhaustion with >30 concurrent operations
- Mitigation: Session auto-close in processors

**4. Single Worker Instance**:
- Current: 1 worker with 5 concurrent jobs
- Risk: Single point of failure
- Improvement: Multiple worker instances (already supported)

### Scalability Improvements

**Horizontal Scaling**:
- Multiple worker instances: Supported via `FOR UPDATE SKIP LOCKED`
- Each worker has unique ID
- No coordination required

**Vertical Scaling**:
- Increase `max_concurrent_jobs` (current: 5)
- Risk: Database connection pool exhaustion
- Recommendation: Max 10 concurrent jobs per worker

**Caching Opportunities**:
- User lookups: Cache email → user ID mappings
- Pilot user checks: Cache pilot user list
- Meeting metadata: Already cached in database

**Database Optimization**:
- Indexes: Well-indexed (meeting_id, status, priority)
- Partitioning: Not needed (moderate data volume)
- Archival: Consider archiving old meetings (>90 days)

### Monitoring & Observability

**Health Checks**:
- Web dashboard: `/health`, `/health/deep`
- Database connectivity
- Graph API connectivity
- Claude API connectivity

**Metrics** (Not Implemented):
- Job queue depth
- Job processing time (by type)
- API latency (Graph, Claude)
- Error rates
- Recommendation: Add Prometheus metrics

**Alerting** (Not Implemented):
- Job queue backlog
- Worker failures
- API errors
- Recommendation: Set up alerting (email, Slack)

---

## Future Enhancements

### Short-Term (< 1 month)

1. **Webhook Signature Validation**
   - Verify webhook authenticity
   - Prevent spoofed notifications

2. **Metrics & Monitoring**
   - Prometheus metrics endpoint
   - Grafana dashboards
   - Alerting setup

3. **Log Rotation**
   - Configure logrotate
   - Archive old logs

4. **Database Archival**
   - Archive meetings >90 days
   - Reduce query load

### Medium-Term (1-3 months)

1. **Multi-Worker Support**
   - Deploy multiple worker instances
   - Load balancing
   - Worker health monitoring

2. **Cost Optimization**
   - Switch to Haiku for cost savings (already in progress)
   - Implement prompt caching for repeat meetings
   - Monitor API costs per meeting

3. **User Management**
   - Self-service opt-in/opt-out via web dashboard
   - Email preference management
   - Subscription management

4. **Enhanced Summaries**
   - Custom summary templates
   - User feedback on summary quality
   - A/B testing of prompts

### Long-Term (3-6 months)

1. **Microsoft Teams App**
   - In-meeting bot
   - Adaptive cards in chat
   - Direct user interactions

2. **Analytics Dashboard**
   - Meeting trends
   - Summary quality metrics
   - User engagement

3. **Multi-Tenant Support**
   - Support multiple organizations
   - Tenant isolation
   - Per-tenant configuration

4. **Integrations**
   - Slack distribution
   - SharePoint document creation
   - Task management (Planner, Jira)

---

## Appendix

### API Endpoints Used

**Microsoft Graph API**:
- `GET /communications/callRecords`: List all meetings
- `GET /communications/callRecords/{id}`: Get callRecord details
- `GET /communications/callRecords/{id}/sessions`: Get call sessions
- `GET /users/{id}/onlineMeetings`: List user's meetings
- `GET /users/{id}/onlineMeetings/getAllTranscripts`: Get all transcripts for user
- `GET /users/{id}/onlineMeetings/{meetingId}/transcripts`: Get transcripts for meeting
- `GET /users/{id}/onlineMeetings/{meetingId}/recordings`: Get recordings
- `GET /chats/{id}/messages`: Get chat messages
- `POST /chats/{id}/messages`: Post to chat
- `POST /users/{id}/sendMail`: Send email
- `GET /users/{id}`: Get user details
- `GET /users/{id}/photos/{size}/$value`: Get user photo

**Claude API**:
- `POST /v1/messages`: Generate summary (Messages API)

### Backfill Scripts

Located in `/scripts/` directory for data enrichment:

**backfill_chat_id.py**:
- Extracts `chat_id` from `join_url` for existing meetings
- Enables chat event detection (`recording_started`, `transcript_available`)
- Parses `19:...@thread.v2` format from Teams URLs
- Run: `python scripts/backfill_chat_id.py`

**backfill_azure_ad.py**:
- Fetches Azure AD properties for existing participants
- Populates: job_title, department, office_location, company_name
- Deduplicates Graph API calls by email
- Run: `python scripts/backfill_azure_ad.py`

### Common Issues & Troubleshooting

**Issue**: Transcripts not found (TranscriptNotFoundError)
**Cause**: Transcript not yet available (Teams takes 5-60 minutes)
**Solution**: Retry logic waits up to 1hr 45min, optimized by chat event signals

**Issue**: 403 error accessing organizer's transcripts
**Cause**: Application permissions don't include organizer's transcripts
**Solution**: Fallback to pilot user who attended meeting

**Issue**: Jobs stuck in "running" status
**Cause**: Worker crashed, stale heartbeat
**Solution**: Self-healing cleanup after 15 minutes

**Issue**: Duplicate meetings created
**Cause**: Webhook and backfill both processed same callRecord
**Solution**: ProcessedCallRecord deduplication table

**Issue**: Email not sent to user
**Cause**: User hasn't opted-in (default opt-out)
**Solution**: User must send chat command to opt-in

### Development Workflow

**Local Development**:
```bash
# Activate venv
source venv/bin/activate

# Run services locally
python -m src.main run --loop        # Worker
python -m src.main serve --port 8000 # Web dashboard

# Run tests
pytest tests/

# Database init
python -m src.main db init

# Check health
python -m src.main health
```

**Production Deployment**:
```bash
# Pull latest code
git pull

# Restart services
systemctl --user restart teams-notetaker-poller
systemctl --user restart teams-notetaker-web

# Check logs
journalctl --user -u teams-notetaker-poller -f
```

### References

- [Microsoft Graph API Documentation](https://learn.microsoft.com/en-us/graph/)
- [Claude API Documentation](https://docs.anthropic.com/)
- [Azure Relay Hybrid Connections](https://learn.microsoft.com/en-us/azure/azure-relay/relay-hybrid-connections-protocol)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
