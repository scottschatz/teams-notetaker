# Teams Meeting Transcript Summarizer

🤖 **Production-ready AI-powered meeting summary system** for Microsoft Teams. Automatically discovers meetings, generates structured summaries using Claude AI, and distributes via email.

**Status**: ✅ **Production Ready** - Fully deployed with auto-recovery and self-healing capabilities

---

## 🎯 Key Features

### Enhanced AI Summarization (Claude Sonnet 4.5)

**6 Structured Extraction Stages:**
- ✅ **Action Items** - Assignees, deadlines, context, timestamps
- 🎯 **Key Decisions** - With reasoning, impact, and rationale
- 💡 **Key Moments** - Critical highlights with clickable timestamps
- 📊 **Key Numbers** - All financial metrics, percentages, quantities
- 📝 **Executive Summary** - Variable length (50-125 words based on complexity)
- 💬 **Discussion Notes** - Thematic narrative with 2-3 subheadings

**Cost-Optimized**: Prompt caching enabled (90% cost savings on transcript tokens)

### Email Distribution

**Professional HTML Emails** with enhanced formatting:
- 📸 Profile pictures for all attendees (circular 48x48)
- 🎨 **Bold + blue participant names** throughout all sections
- 📊 Key Numbers section (all metrics in one place)
- ⚡ Key Moments with clickable timestamps to recording
- 📋 Compact attendees display (first 5 detailed, rest simplified)
- 🔗 Deep links to Teams chat for transcript/recording/files
- 📅 Only shows actual call duration (no scheduled clutter)

**Smart Distribution**:
- Sent to all meeting participants automatically
- Respects opt-out preferences
- Includes email preferences footer

### Automatic Discovery & Processing

- 🔍 **5-minute polling** of Microsoft Teams for new meetings
- 🎯 **Pilot mode** - Test with selected users first
- ⚙️ **Async job queue** - Process 5-10 meetings concurrently
- 🔄 **Auto-retry** with exponential backoff
- 🛡️ **Self-healing** - Recovers stale/orphaned jobs automatically

### Production Robustness

**Automatic Recovery** (NEW):
- ✅ Recovers jobs stuck >15 minutes (stale heartbeat)
- ✅ Cleans up orphaned jobs (failed parent dependencies)
- ✅ Periodic cleanup every 60 seconds
- ✅ Increments retry count on recovery

**Auto-Start**:
- ✅ Both services start automatically with WSL boot
- ✅ User linger enabled for unattended operation
- ✅ Auto-restart on failure (10-second delay)

**Resource Management**:
- Memory limits: Poller (1GB), Web (512MB)
- CPU quota: 200% for poller
- Logging: journalctl + file logs

### Web Dashboard

- 📊 Real-time monitoring of job queue status
- 📝 Browse meetings and summaries
- 👥 Manage pilot users
- 🔐 Azure AD SSO + password authentication
- 🔍 Health check endpoints

### Re-Summarization Support

- 🔄 Generate multiple summary versions with custom instructions
- 📝 Version tracking (v1, v2, v3...)
- ✉️ Resend emails with new formatting
- 🔗 Previous versions linked via superseded_by

---

## 🏗️ Architecture

```
┌─────────────────┐
│  MS Teams API   │
└────────┬────────┘
         │ (5min poll)
         v
┌─────────────────┐      ┌──────────────┐
│ Meeting Poller  │─────>│  PostgreSQL  │
└─────────────────┘      │  Job Queue   │
                         └──────┬───────┘
                                │
         ┌──────────────────────┴───────────────────┐
         v                                          v
┌─────────────────┐                        ┌─────────────────┐
│  Job Worker     │                        │  Web Dashboard  │
│  (5 concurrent) │                        │  (Port 8000)    │
└────────┬────────┘                        └─────────────────┘
         │
         v
┌─────────────────┐      ┌──────────────┐
│  Claude API     │      │  Graph API   │
│  (Sonnet 4.5)   │      │  (Email)     │
└─────────────────┘      └──────────────┘
```

### Technology Stack

- **Backend**: FastAPI + Python 3.12
- **Database**: PostgreSQL 15+ with SQLAlchemy ORM
- **Job Queue**: Database-backed with `FOR UPDATE SKIP LOCKED`
- **AI**: Anthropic Claude API (Sonnet 4.5)
- **APIs**: Microsoft Graph API (v1.0)
- **Authentication**: JWT + Azure AD SSO (MSAL)
- **Deployment**: WSL2 + Systemd user services
- **Email**: HTML templates with markdown2 conversion

---

## 📊 Current Production Metrics

```
Job Queue Health:
- Completed: 57 jobs
- Failed: 5 jobs (9% failure rate)
- Active Services: 2/2 ✅

Performance:
- Average processing: ~60 seconds per meeting
- API Cost: ~$0.06 per meeting (with caching)
- Token usage: ~34K input, ~1.3K output per meeting

System Status:
✅ Auto-start on WSL boot
✅ Auto-restart on failure
✅ Stale job recovery
✅ Orphaned job cleanup
✅ Resource limits enforced
```

---

## 🚀 Installation & Deployment

### Prerequisites

- Python 3.11 or higher
- PostgreSQL 12+ (running in WSL)
- **Azure AD Application** with permissions:
  - `OnlineMeetings.Read.All` - Discover meetings
  - `OnlineMeetingTranscript.Read.All` - Download transcripts
  - `OnlineMeetingRecording.Read.All` - Recording metadata
  - `Calendars.Read` - User calendars
  - `Mail.Send` - Send emails
  - `User.Read.All` - User info and photos
- **Claude API key** from Anthropic

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
# Edit .env with your credentials (Azure, Claude, PostgreSQL)

# 4. Initialize database
python -m src.main db init

# 5. Deploy services (auto-start with WSL)
./deploy_services.sh
```

### Service Management

```bash
# Check service status
systemctl --user status teams-notetaker-poller.service
systemctl --user status teams-notetaker-web.service

# View logs
journalctl --user -u teams-notetaker-poller.service -f
journalctl --user -u teams-notetaker-web.service -f

# Restart services
systemctl --user restart teams-notetaker-poller.service

# Stop services
systemctl --user stop teams-notetaker-poller.service teams-notetaker-web.service
```

---

## 📖 Configuration

### Pilot Mode

Test with specific users before org-wide rollout:

```yaml
# config.yaml
app:
  pilot_mode: true
  pilot_users:
    - "user1@company.com"
    - "user2@company.com"
```

### Email Customization

```yaml
email:
  from_address: "noreply@company.com"
  from_name: "Meeting Notetaker"
  subject_template: "📝 Meeting Summary: {meeting_subject}"
```

### Job Queue Settings

```yaml
app:
  max_concurrent_jobs: 5
  job_timeout_minutes: 10
  discovery_interval_minutes: 5
```

---

## 🔧 Advanced Features

### Re-Summarization with Custom Instructions

```bash
# Generate new version with custom instructions
python -m src.main resummary --meeting-id 123 \
  --instructions "Focus on technical details and code changes"
```

### Manual Job Creation

```bash
# Process specific meeting
python -m src.main enqueue --meeting-id 123

# Resend email only (no new summary)
python -m src.main distribute --meeting-id 123
```

### Database Cleanup

```bash
# Remove completed jobs older than 90 days
python -m src.main db cleanup --days 90
```

---

## 📝 API Endpoints

### Web Dashboard (Port 8000)

- `GET /` - Dashboard home
- `GET /meetings` - Browse meetings
- `GET /meetings/{id}` - Meeting details
- `GET /health` - Health check
- `GET /api/stats` - Queue statistics

### Authentication

- `POST /auth/login` - Password login
- `GET /auth/sso` - Azure AD SSO
- `POST /auth/logout` - Logout

---

## 🛡️ Security & Privacy

- **No data stored externally** - All data in your PostgreSQL database
- **Azure AD authentication** - Respects your organization's security policies
- **Transcript access** - Follows Microsoft Teams permissions
- **Opt-out support** - Users can disable summaries via email commands
- **Email links respect permissions** - Teams deep links honor user access rights

---

## 📚 Documentation

- [DEPLOYMENT.md](DEPLOYMENT.md) - Detailed deployment instructions
- [CLAUDE.md](CLAUDE.md) - AI development session notes
- [APPLICATION_ACCESS_POLICY_SETUP.md](APPLICATION_ACCESS_POLICY_SETUP.md) - Azure AD setup
- [PERMISSIONS_SETUP.md](PERMISSIONS_SETUP.md) - Graph API permissions guide

---

## 🐛 Troubleshooting

### Services Not Starting

```bash
# Check service status
systemctl --user status teams-notetaker-poller.service

# Check logs
journalctl --user -u teams-notetaker-poller.service -n 50

# Clear Python cache and restart
find . -type d -name __pycache__ -exec rm -rf {} +
systemctl --user restart teams-notetaker-poller.service
```

### Jobs Stuck in Queue

The system automatically recovers stale jobs (>15min) and cleans up orphaned jobs every 60 seconds. Check logs:

```bash
journalctl --user -u teams-notetaker-poller.service | grep -i "recovered\|orphaned"
```

### Database Connection Issues

```bash
# Check PostgreSQL is running
sudo service postgresql status

# Test database connection
python -m src.main db health
```

---

## 🔄 Updates & Maintenance

### Updating Code

```bash
# Pull latest changes
git pull origin main

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
find . -name "*.pyc" -delete

# Restart services
systemctl --user restart teams-notetaker-poller.service
systemctl --user restart teams-notetaker-web.service
```

### Database Migrations

```bash
# Run migrations
python -m src.main db migrate

# Or manually with psql
psql -h localhost -U postgres -d teams_notetaker -f migrations/add_key_numbers_column.sql
```

---

## 📊 Monitoring

### Queue Health

```bash
# Check job queue stats
python -m src.main stats
```

### Log Monitoring

```bash
# Follow poller logs
journalctl --user -u teams-notetaker-poller.service -f

# Filter for errors
journalctl --user -u teams-notetaker-poller.service | grep ERROR

# Show last 100 lines
journalctl --user -u teams-notetaker-poller.service -n 100
```

---

## 🤝 Contributing

Contributions welcome! This project was built with Claude Code.

---

## 📄 License

[Your License Here]

---

## 🙏 Acknowledgments

- Built with [Claude Code](https://claude.com/claude-code)
- Powered by [Anthropic Claude API](https://www.anthropic.com/api)
- Microsoft Graph API integration
- FastAPI framework

---

**Status**: ✅ Production Ready (Last Updated: 2025-12-16)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
