# Teams Meeting Transcript Summarizer

Production-ready AI-powered meeting summary system for Microsoft Teams. Automatically discovers meetings via webhooks, generates structured summaries using Claude AI, and distributes via email to subscribed users.

**Status**: Production Ready - Deployed on WSL2 with systemd services

---

## Table of Contents

- [Key Features](#key-features)
- [Architecture](#architecture)
- [Recent Features](#recent-features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Web Dashboard](#web-dashboard)
- [CLI Commands](#cli-commands)
- [API Endpoints](#api-endpoints)
- [Subscription System](#subscription-system)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [License](#license)

---

## Key Features

### Intelligent Meeting Discovery
- **Webhook-Driven**: Real-time notifications via Azure Relay Hybrid Connections
- **Automatic Backfill**: Gap detection on startup catches missed meetings
- **Deduplication**: Prevents duplicate processing from multiple sources
- **Org-Wide Coverage**: Single webhook for entire organization

### Enhanced AI Summarization (Claude Haiku 4.5)
- **Action Items**: Assignees, deadlines, context, and timestamps
- **Key Decisions**: Reasoning, impact, and participants
- **Key Moments**: Critical highlights with clickable timestamps to recording
- **Key Numbers**: All financial metrics, percentages, and quantities
- **Executive Summary**: Variable length (50-125 words) based on meeting complexity
- **Discussion Notes**: Thematic narrative with organized sub-sections
- **Cost-Optimized**: Uses Claude Haiku 4.5 (~$0.004 per meeting)
- **Gemini Available**: Google Gemini 3 Flash available as alternative (currently disabled due to quality issues)

### Professional Email Distribution
- **Profile Photos**: Circular 48x48 photos for all attendees
- **Formatted Names**: Bold + blue participant names throughout
- **Clickable Timestamps**: Direct links to specific moments in recording
- **SharePoint Links**: Deep links to transcript and recording (respects permissions)
- **Compact Display**: First 5 attendees detailed, rest simplified
- **Invitees Section**: Shows who was invited but didn't attend
- **Optional Transcript Attachment**: Include VTT file in email

### Subscription System
- **Email Opt-In**: Users subscribe by emailing note.taker@townsquaremedia.com
- **Automatic Processing**: Inbox monitor processes subscribe/unsubscribe requests
- **Unsubscribe Links**: One-click unsubscribe in every email
- **Admin Management**: Web dashboard for user management
- **Email Aliases**: Supports multiple email addresses per user

### Chat Event Intelligence
- **Chat ID Extraction**: Automatically extracts chat ID from meeting join URLs (`19:...@thread.v2`)
- **Recording Signals**: Monitors Teams chat for recording/transcript events
- **Adaptive Retries**: Optimizes retry timing based on availability signals (`recording_started`, `transcript_available` flags)
- **Immediate Processing**: Transcript fetched as soon as available
- **Conservative Fallback**: 15/30/60-minute retry schedule if no signals
- **Backfill Support**: Script available to extract chat IDs from existing meetings

### Web Dashboard (FastAPI + Alpine.js + Tailwind)
- **Meetings Browser**: Sortable table with column filters and pagination
- **Live Filtering**: Filter by status, source, organizer, model, recording/transcript availability
- **1:1 Call Filtering**: Option to include/exclude peerToPeer (1:1) calls
- **Download Options**: VTT transcripts and Markdown summaries
- **Diagnostics**: Force backfill, inbox monitoring, test emails
- **User Management**: Subscriber counts, meeting statistics, time-period filtering
- **Admin Tools**: Email alias management, manual processing controls
- **Azure AD Properties**: View participant job titles, departments, locations

### Production Robustness
- **Auto-Start**: systemd services start with WSL boot
- **Self-Healing**: Recovers stale jobs (>15 min heartbeat gap)
- **Orphaned Job Cleanup**: Handles failed dependencies
- **Resource Limits**: Memory (1GB poller, 512MB web), CPU quotas
- **Health Monitoring**: API connectivity checks and deep health endpoints

---

## Architecture

### High-Level Flow

```
Microsoft Teams Meeting Ends
         ↓
Azure Relay Webhook (CallRecords notification)
         ↓
CallRecordsWebhookHandler
  - Extract participants
  - Check for subscribers
  - Deduplicate via ProcessedCallRecord
         ↓
Create Meeting + Enqueue fetch_transcript job
         ↓
Job Worker (async, 5 concurrent)
         ↓
┌────────────────┬────────────────┬────────────────┐
│ fetch_transcript│ generate_summary│   distribute   │
│  - Get VTT     │  - Claude API  │  - Filter subs │
│  - Parse       │  - 6 extractions│  - Send email  │
│  - Chat signals│  - Markdown    │  - Teams chat  │
└────────────────┴────────────────┴────────────────┘
```

### Technology Stack

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy
- **Database**: PostgreSQL 15+ with JSONB support
- **Job Queue**: Database-backed with `FOR UPDATE SKIP LOCKED`
- **AI**: Anthropic Claude API (Haiku 4.5)
- **Microsoft APIs**: Graph API v1.0 + beta (transcripts)
- **Authentication**: MSAL (Graph API), JWT (web dashboard)
- **Deployment**: WSL2 + systemd user services
- **Frontend**: Jinja2 templates + Alpine.js + Tailwind CSS

### Key Components

- **`src/webhooks/call_records_handler.py`**: Webhook processing, meeting creation
- **`src/jobs/processors/transcript.py`**: VTT fetching, chat signal detection
- **`src/jobs/processors/summary.py`**: Claude AI summarization
- **`src/jobs/processors/distribution.py`**: Email/chat distribution
- **`src/inbox/monitor.py`**: Email inbox monitoring for subscribe/unsubscribe
- **`src/graph/client.py`**: Graph API wrapper with auth and retries
- **`src/core/database.py`**: SQLAlchemy models and database manager
- **`src/web/`**: FastAPI dashboard with routers and templates
- **`src/jobs/worker.py`**: Async job worker with self-healing

---

## Recent Features

### December 2025 Updates

#### Column Header Filters
- Dropdown filters on Meetings table columns
- Filter by: Status, Source, Organizer, Model, Rec/Tsc availability
- Client-side filtering via Alpine.js
- URL state preservation for bookmarking

#### Subscriber Statistics
- Meeting attendance counts per user
- Summary delivery counts per user
- Time-period filtering: 7/30/90 days, All time
- Real-time count updates

#### Download Endpoints
- Download VTT transcripts: `/meetings/{id}/transcript/download`
- Download Markdown summaries: `/meetings/{id}/summary/download`
- Permission-aware access control

#### Enhanced Email Features
- Optional transcript attachment (VTT file)
- Transcript-only emails (no summary required)
- Improved formatting and layout
- Better mobile responsiveness

#### Chat Event Detection & ID Extraction
- Extracts chat ID from join URLs (`19:...@thread.v2` format)
- Monitors Teams chat for recording/transcript events
- Auto-sets `recording_started` and `transcript_available` flags
- Optimizes retry timing based on signals
- Reduces unnecessary API calls
- Backfill script: `/scripts/backfill_chat_id.py`

#### Azure AD User Properties
- Enriches participant data with Azure AD properties
- Fetches: job title, department, office location, company name
- Stored in `meeting_participants` table
- Used for enhanced email formatting
- Backfill script: `/scripts/backfill_azure_ad.py`

#### 1:1 Call Filtering
- Filter meetings by call type (groupCall, peerToPeer, etc.)
- Web dashboard excludes 1:1 calls by default
- `include_one_on_one` query parameter for control
- Stored in `call_type` column

---

## Installation

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 12+** (running in WSL)
- **Azure AD Application** with permissions:
  - `CallRecords.Read.All`
  - `OnlineMeetings.Read.All`
  - `OnlineMeetingTranscript.Read.All`
  - `Chat.Read.All`
  - `Mail.Send`
  - `Mail.Read` (for inbox monitoring)
  - `User.Read.All`
- **Claude API Key** from Anthropic (required)
- **Google API Key** (optional, for Gemini - currently disabled)
- **Azure Relay** (Hybrid Connection for webhooks)

### Quick Start

```bash
# 1. Clone repository
git clone https://github.com/scottschatz/teams-notetaker.git
cd teams-notetaker

# 2. Set up Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 4. Initialize database
python -m src.main db init

# 5. Deploy services (auto-start with WSL)
./scripts/deploy_services.sh
```

### Service Management

```bash
# Check service status
systemctl --user status teams-notetaker-poller
systemctl --user status teams-notetaker-web

# View logs
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f

# Restart services
systemctl --user restart teams-notetaker-poller teams-notetaker-web

# Stop services
systemctl --user stop teams-notetaker-poller teams-notetaker-web
```

---

## Configuration

### Environment Variables (.env)

```bash
# Microsoft Graph API
GRAPH_CLIENT_ID=your-app-id
GRAPH_CLIENT_SECRET=your-secret
GRAPH_TENANT_ID=your-tenant-id

# Claude API (required)
ANTHROPIC_API_KEY=your-api-key

# Google API (optional, for Gemini - currently disabled)
GOOGLE_API_KEY=your-google-api-key

# Database
DATABASE_URL=postgresql://user:password@localhost/teams_notetaker

# Azure Relay (Webhooks)
AZURE_RELAY_NAMESPACE=yournamespace.servicebus.windows.net
AZURE_RELAY_HYBRID_CONNECTION=teams-webhooks
AZURE_RELAY_KEY_NAME=RootManageSharedAccessKey
AZURE_RELAY_KEY=your-relay-key

# Web Dashboard
JWT_SECRET_KEY=your-secret-key
```

### Application Settings (config.yaml)

```yaml
# Polling & Discovery
polling_interval_minutes: 5
lookback_hours: 48

# Job Processing
max_concurrent_jobs: 5
job_timeout_minutes: 10

# AI Model
claude_model: "claude-haiku-4-5"
summary_max_tokens: 2000

# Distribution
email_enabled: true
email_from: "note.taker@townsquaremedia.com"
teams_chat_enabled: true

# Inbox Monitoring
inbox_check_interval_seconds: 60
inbox_lookback_minutes: 60
inbox_delete_processed_commands: true
```

---

## Usage

### For End Users

#### Subscribe to Meeting Summaries
Send an email to `note.taker@townsquaremedia.com` with subject line "subscribe". You'll receive a confirmation and start getting meeting summaries.

#### Unsubscribe
Click the unsubscribe link in any summary email, or send an email to `note.taker@townsquaremedia.com` with subject "unsubscribe".

#### Access Web Dashboard
Navigate to `http://localhost:8000` (or your configured URL) to:
- Browse past meetings
- Download transcripts and summaries
- View your subscription status

### For Administrators

#### Add Users
```bash
# Via CLI
python -m src.main subscribers add user@company.com --name "User Name"

# Via SQL
INSERT INTO meeting_subscribers (email, display_name, is_subscribed)
VALUES ('user@company.com', 'User Name', true);
```

#### Force Backfill
```bash
# Last 24 hours
python -m src.main backfill --hours 24

# Or via web dashboard
# Navigate to Diagnostics → Force Backfill
```

#### Monitor System Health
```bash
# CLI health check
python -m src.main health

# Web endpoint
curl http://localhost:8000/health

# View queue statistics
curl http://localhost:8000/api/stats
```

---

## Web Dashboard

Access at `http://localhost:8000`

### Pages

#### Meetings (`/meetings`)
- Sortable, filterable table of all meetings
- Column filters: Status, Source, Organizer, Model, Rec/Tsc
- Pagination (20/50/100 per page)
- Quick actions: Process, Download, View Details
- Shows: Subject, time, duration, participants, status, summaries

#### Meeting Detail (`/meetings/{id}`)
- Full meeting metadata
- Transcript preview
- Summary with all extractions (action items, decisions, etc.)
- Participant list with attendance
- Job history and logs
- Download options

#### Diagnostics (`/diagnostics`)
- Force backfill with custom hours
- Backfill history viewer
- Inbox monitoring status
- Send test emails
- Job queue statistics
- System health checks

#### Admin - Users (`/admin/users`)
- Subscriber list with search
- Meeting counts per user (time-filtered)
- Summary counts per user (time-filtered)
- Subscribe/unsubscribe actions
- Bulk import/export

#### Admin - Email Aliases (`/admin/email-aliases`)
- Map alternate emails to primary accounts
- Prevent duplicate subscriptions
- Consolidate user data

---

## CLI Commands

### Discovery & Processing
```bash
# Run worker with one-time backfill
python -m src.main run --loop

# Continuous polling (legacy)
python -m src.main run --poll-loop

# Backfill specific hours
python -m src.main backfill --hours 24
```

### Database Management
```bash
# Initialize database
python -m src.main db init

# Run migrations
python -m src.main db migrate

# Check database health
python -m src.main db health
```

### Subscriber Management
```bash
# Add subscriber
python -m src.main subscribers add email@company.com --name "Name"

# Remove subscriber
python -m src.main subscribers remove email@company.com

# List subscribers
python -m src.main subscribers list

# Import from CSV
python -m src.main subscribers import users.csv
```

### Webhooks
```bash
# Start webhook listener
python -m src.main webhooks listen

# Check webhook status
python -m src.main webhooks status

# List active subscriptions
python -m src.main webhooks list
```

### Web Dashboard
```bash
# Start web server
python -m src.main serve --port 8000

# With auto-reload (development)
python -m src.main serve --port 8000 --reload
```

### Utilities
```bash
# Check API health
python -m src.main health

# View configuration
python -m src.main config show

# View queue statistics
python -m src.main stats
```

---

## API Endpoints

### Public Endpoints
- `GET /` - Dashboard home
- `GET /health` - Basic health check
- `GET /login` - Login page
- `POST /auth/login` - Authenticate user

### Authenticated Endpoints
- `GET /meetings` - List meetings
- `GET /meetings/{id}` - Meeting details
- `GET /meetings/{id}/transcript/download` - Download VTT
- `GET /meetings/{id}/summary/download` - Download Markdown
- `GET /diagnostics` - Diagnostics page
- `POST /diagnostics/backfill` - Force backfill
- `GET /admin/users` - User management
- `GET /admin/email-aliases` - Email alias management

### Health & Monitoring
- `GET /health` - Basic health check (200 OK)
- `GET /health/deep` - Detailed health (DB, APIs)
- `GET /api/stats` - Queue statistics (JSON)

---

## Subscription System

### How It Works

1. **User Subscribes**: Sends email to note.taker@townsquaremedia.com with subject "subscribe"
2. **Inbox Monitor**: Detects email, validates, adds to `meeting_subscribers` table
3. **Confirmation**: Sends welcome email with instructions
4. **Meeting Processing**: Only subscribers receive summary emails
5. **Unsubscribe**: Click link in email or send "unsubscribe" email

### Subscription States

- **Subscribed** (`is_subscribed = true`): Receives all meeting summaries
- **Unsubscribed** (`is_subscribed = false`): Does not receive emails
- **Admin Override** (`is_admin_managed = true`): Admin-added, cannot self-unsubscribe

### Email Aliases

Multiple email addresses can map to a single subscriber:
- Primary: `john.doe@company.com`
- Aliases: `j.doe@company.com`, `jdoe@company.com`
- Prevents duplicate subscriptions
- Consolidates meeting attendance under one user

---

## Development

### Local Development Setup

```bash
# Activate virtual environment
source venv/bin/activate

# Install dev dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run with auto-reload
python -m src.main serve --port 8000 --reload
```

### Code Structure

```
src/
├── ai/              # Claude API integration
├── auth/            # Web dashboard authentication
├── cli/             # Click commands
├── core/            # Database models, config
├── discovery/       # Meeting discovery (backfill)
├── graph/           # Microsoft Graph API client
├── inbox/           # Email inbox monitoring
├── jobs/            # Job queue and processors
├── preferences/     # User preference management (deprecated)
├── utils/           # Utilities
├── web/             # FastAPI dashboard
└── webhooks/        # Azure Relay webhook handling
```

### Adding Features

#### New Job Processor
1. Create class in `src/jobs/processors/`
2. Inherit from `BaseJobProcessor`
3. Implement `async def process(self, job: JobQueue) -> Dict[str, Any]`
4. Register in `ProcessorRegistry`

#### New Web Route
1. Create router in `src/web/routers/`
2. Create template in `src/web/templates/`
3. Register router in `src/web/app.py`

#### Database Migration
1. Update models in `src/core/database.py`
2. Create SQL file in `migrations/`
3. Run migration manually or via CLI

### Testing

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_backfill.py -v

# Run with coverage
pytest --cov=src tests/

# Note: Tests use SQLite, JSONB compatibility patch applied
```

---

## Troubleshooting

### Services Not Starting

```bash
# Check service status
systemctl --user status teams-notetaker-poller

# View logs
journalctl --user -u teams-notetaker-poller -n 100

# Clear Python cache and restart
find . -type d -name __pycache__ -exec rm -rf {} +
systemctl --user restart teams-notetaker-poller
```

### Jobs Stuck in Queue

System automatically recovers stale jobs (>15min heartbeat gap) every 60 seconds. Check logs:

```bash
journalctl --user -u teams-notetaker-poller | grep -i "recovered\|orphaned"
```

Manual recovery:
```sql
UPDATE job_queue SET status = 'pending', worker_id = NULL
WHERE status = 'running' AND heartbeat_at < NOW() - INTERVAL '15 minutes';
```

### Transcripts Not Found

- **Cause**: Microsoft takes 5-60 minutes to process transcripts after meeting
- **Solution**: Retry logic with chat signal detection handles this automatically
- **Check**: Meeting chat for recording/transcript availability events

### Email Not Sent

- **Cause 1**: User not subscribed
- **Solution**: User must send "subscribe" email to note.taker@townsquaremedia.com
- **Cause 2**: Email in spam/junk folder
- **Solution**: Add note.taker@townsquaremedia.com to safe senders

### Database Connection Issues

```bash
# Check PostgreSQL
sudo service postgresql status

# Test connection
psql -h localhost -U postgres -d teams_notetaker -c "SELECT 1"

# Restart PostgreSQL
sudo service postgresql restart
```

### Webhook Not Receiving Notifications

```bash
# Check Azure Relay connection
journalctl --user -u teams-notetaker-webhook -f | grep "Connected to Azure Relay"

# Verify subscription
python -m src.main webhooks status

# Check subscription expiry
python -m src.main webhooks list
```

---

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - AI assistant development guidance
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Detailed system architecture
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Deployment instructions
- **[PERMISSIONS_SETUP.md](PERMISSIONS_SETUP.md)** - Azure AD permissions guide
- **[AZURE_RELAY_SETUP.md](AZURE_RELAY_SETUP.md)** - Webhook setup guide
- **[WEBHOOK_IMPLEMENTATION.md](WEBHOOK_IMPLEMENTATION.md)** - Webhook details
- **[docs/OPT_IN_OUT_SYSTEM.md](docs/OPT_IN_OUT_SYSTEM.md)** - Subscription system (deprecated features)

---

## Monitoring & Observability

### Health Checks
```bash
# Basic health
curl http://localhost:8000/health

# Deep health (DB + APIs)
curl http://localhost:8000/health/deep

# Queue statistics
curl http://localhost:8000/api/stats | jq
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
-- Recent job status
SELECT job_type, status, COUNT(*)
FROM job_queue
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY job_type, status;

-- Subscriber count
SELECT COUNT(*) FROM meeting_subscribers WHERE is_subscribed = true;

-- Meetings without summaries
SELECT id, subject, start_time
FROM meetings
WHERE status = 'completed' AND has_summary = false
ORDER BY start_time DESC LIMIT 10;
```

---

## Performance Metrics

### Current Production Stats
- **Meetings Processed**: ~400/month
- **Subscribers**: Growing user base
- **Job Success Rate**: >90%
- **Average Processing Time**: ~60 seconds per meeting
- **API Cost**: ~$0.004 per meeting (Claude Haiku 4.5)
- **Token Usage**: ~34K input, ~1.3K output per meeting

### Scalability
- **Concurrent Jobs**: 5 (configurable up to 10)
- **Throughput**: ~30 meetings/hour
- **Database Pool**: 10 base + 20 overflow connections
- **Horizontal Scaling**: Supports multiple worker instances

---

## Security

### Credential Management
- Secrets stored in `.env` (gitignored, 600 permissions)
- MSAL token caching (1-hour TTL, auto-refresh)
- JWT tokens for web sessions
- No hardcoded credentials in code

### Data Protection
- All API calls over HTTPS
- SharePoint URLs respect Microsoft 365 permissions
- Email links honor Teams access rights
- Transcript content stored locally only

### Access Control
- Web dashboard requires authentication
- Role-based access control (RBAC)
- API endpoints require valid JWT token
- Admin functions restricted to admin role

---

## License

[Specify your license here - MIT, Apache 2.0, Proprietary, etc.]

---

## Contributing

Contributions welcome! This project was built with Claude Code.

### Development Process
1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Submit pull request

### Code Standards
- Type hints throughout
- Comprehensive docstrings
- Error handling at all levels
- Logging with appropriate levels

---

## Acknowledgments

- Built with [Claude Code](https://claude.com/claude-code)
- Powered by [Anthropic Claude API](https://www.anthropic.com/api)
- Microsoft Graph API integration
- FastAPI framework
- SQLAlchemy ORM

---

## Support

### Getting Help
- Check [Documentation](#documentation) section
- Review [Troubleshooting](#troubleshooting) guide
- Check logs: `journalctl --user -u teams-notetaker-poller -f`
- GitHub Issues: https://github.com/scottschatz/teams-notetaker/issues

### Reporting Bugs
Please include:
- Error messages from logs
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, etc.)

---

**Status**: Production Ready
**Last Updated**: 2025-12-22
**Version**: 3.0

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
