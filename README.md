# Teams Meeting Transcript Summarizer

AI-powered meeting summary and distribution system for Microsoft Teams. Automatically discovers meetings, generates summaries using Claude AI, and distributes via email and Teams chat.

## Features

- 🔍 **Automatic Discovery**: Polls Microsoft Teams every 5 minutes for new meetings
- 🤖 **AI Summarization**: Uses Claude API to generate concise, actionable summaries
- 📧 **Email Distribution**: Sends HTML-formatted summaries to all meeting participants
- 💬 **Teams Chat Integration**: Posts summaries directly to meeting chat threads
- 🎯 **Pilot Mode**: Test with selected users before organization-wide rollout
- 🖥️ **Web Dashboard**: Monitor processing status, manage users, view analytics
- 🔒 **Secure Authentication**: Supports both password and Azure AD SSO
- ⚙️ **Job Queue System**: Asynchronous processing with retry logic and error handling

## Architecture

```
Discovery (5min poll) → Job Queue → Worker (5-10 concurrent jobs) → Distribution
                                         ↓
                           Database (PostgreSQL)
```

### Technology Stack

- **Backend**: FastAPI + Python 3.11+
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Job Queue**: Database-backed with `FOR UPDATE SKIP LOCKED`
- **Authentication**: JWT + Azure AD SSO (MSAL)
- **APIs**: Microsoft Graph API + Anthropic Claude API
- **Deployment**: WSL2 Systemd services

## Prerequisites

- Python 3.11 or higher
- PostgreSQL 12 or higher (running in WSL)
- Azure AD application registration with application permissions:
  - `OnlineMeetings.Read.All`
  - `OnlineMeetingTranscript.Read.All`
  - `Mail.Send`
  - `Chat.ReadWrite.All`
  - `User.Read.All`
- Claude API key from Anthropic

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/teams-notetaker.git
cd teams-notetaker
```

### 2. Set Up Python Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

**Required Environment Variables:**

```bash
# Microsoft Graph API (from Azure Portal)
GRAPH_CLIENT_ID=your-client-id
GRAPH_CLIENT_SECRET=your-client-secret
GRAPH_TENANT_ID=your-tenant-id

# PostgreSQL Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=teams_notetaker
DB_USER=postgres
DB_PASSWORD=your-db-password

# Claude API
CLAUDE_API_KEY=sk-ant-your-api-key

# JWT Secret (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_SECRET_KEY=your-secure-random-key

# Admin users (comma-separated emails)
ADMIN_USERS=your-email@townsquaremedia.com
```

### 4. Set Up Runtime Configuration

```bash
# Copy example config
cp config.yaml.example config.yaml

# Customize settings (optional - defaults work for most cases)
nano config.yaml
```

### 5. Initialize Database

```bash
# Create PostgreSQL database
createdb teams_notetaker

# Initialize schema
python -m src.main db init

# Seed default configuration
python -m src.main db seed-config
```

### 6. Test Connections

```bash
# Verify all services are accessible
python -m src.main health
```

Expected output:
```
✓ Database: Connected
✓ Graph API: Authenticated
✓ Claude API: Connected
```

### 7. Add Pilot Users

```bash
# Add yourself to pilot program
python -m src.main pilot add your-email@townsquaremedia.com

# List pilot users
python -m src.main pilot list
```

## Usage

### Command Line Interface

#### Run Poller and Worker

```bash
# Run once (single discovery cycle)
python -m src.main run

# Run continuously (polls every 5 minutes)
python -m src.main run --loop

# Dry run (discover meetings but don't process)
python -m src.main run --dry-run
```

#### Start Web Dashboard

```bash
# Start web dashboard on port 8000
python -m src.main serve

# Custom port
python -m src.main serve --port 8080
```

#### Start Both Services

```bash
# Start poller/worker + web dashboard
python -m src.main start-all
```

#### Pilot User Management

```bash
# Add user to pilot program
python -m src.main pilot add user@townsquaremedia.com

# List all pilot users
python -m src.main pilot list

# Remove user from pilot program
python -m src.main pilot remove user@townsquaremedia.com
```

#### Database Management

```bash
# Initialize database schema
python -m src.main db init

# Run migrations (Alembic)
python -m src.main db migrate

# Seed default configuration
python -m src.main db seed-config
```

#### Testing

```bash
# Test all connections
python -m src.main health

# Test discovery without processing
python -m src.main run --discover-only

# Process specific meeting
python -m src.main run --meeting-id <graph-meeting-id>

# Test with local transcript file
python -m src.main run --local-transcript path/to/transcript.vtt
```

### Web Dashboard

Access the dashboard at: `http://localhost:8000`

**Features:**

1. **Overview Dashboard**: Real-time stats, processing status, recent activity
2. **Meetings Browser**: Search, filter, and view all processed meetings
3. **Meeting Details**: View transcript, summary, and distribution status
4. **Pilot Users**: Manage pilot program participants (admin only)
5. **Configuration**: Edit runtime settings (admin only)
6. **Analytics**: Charts and reports on processing trends
7. **Health**: System health checks and monitoring

**Login:**
- **Password**: Enter your @townsquaremedia.com email
- **SSO**: Click "Login with Microsoft" for Azure AD authentication

## Deployment (WSL2 Systemd)

### Install Services

```bash
# Run setup script
./deployment/setup-services.sh
```

This will:
1. Install systemd service files to `~/.config/systemd/user/`
2. Enable auto-start on boot
3. Start both services (poller + web)

### Service Management

```bash
# Status
systemctl --user status teams-notetaker-poller
systemctl --user status teams-notetaker-web

# Start
systemctl --user start teams-notetaker-poller
systemctl --user start teams-notetaker-web

# Stop
systemctl --user stop teams-notetaker-poller
systemctl --user stop teams-notetaker-web

# Restart
systemctl --user restart teams-notetaker-poller
systemctl --user restart teams-notetaker-web

# View logs
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f
```

### Accessing from Windows

The web dashboard is accessible from Windows browser:
- Local: `http://localhost:8000`
- WSL2 auto-forwards ports to Windows host

For LAN access:
1. Configure Windows Firewall to allow port 8000
2. Set up port forwarding (see deployment guide)

## Configuration

### Runtime Settings (config.yaml)

Editable via web dashboard or manually:

```yaml
polling_interval_minutes: 5       # How often to poll for meetings
lookback_hours: 48                # How far back to search
pilot_mode_enabled: true          # Only process pilot user meetings
max_concurrent_jobs: 5            # Concurrent job processing
job_timeout_minutes: 10           # Max time per job
email_enabled: true               # Send email summaries
teams_chat_enabled: true          # Post to Teams chat
minimum_meeting_duration_minutes: 5  # Skip short meetings
```

### Pilot Mode

**Pilot Mode ON** (default):
- Only processes meetings where at least one participant is in the `pilot_users` table
- Use for testing and gradual rollout

**Production Mode**:
- Processes all meetings organization-wide (~2,000 users)
- Set `pilot_mode_enabled: false` in config.yaml or via dashboard

### Exclusions

Exclude specific users or domains from processing:

1. Via Database:
   ```sql
   INSERT INTO exclusions (type, value, reason)
   VALUES ('user', 'user@domain.com', 'Personal request');

   INSERT INTO exclusions (type, value, reason)
   VALUES ('domain', 'external-domain.com', 'External partners');
   ```

2. Via Dashboard (coming in Phase 9)

## Development

### Project Structure

```
teams-notetaker/
├── src/
│   ├── core/          # Database models, configuration
│   ├── auth/          # Authentication (password + SSO)
│   ├── graph/         # Microsoft Graph API integration
│   ├── ai/            # Claude API integration
│   ├── jobs/          # Job queue and worker
│   ├── discovery/     # Meeting polling
│   ├── web/           # FastAPI dashboard
│   └── utils/         # Utilities (VTT parser, etc.)
├── tests/             # Unit and integration tests
├── deployment/        # Systemd service files
└── migrations/        # Alembic database migrations
```

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_auth.py
```

### Code Quality

```bash
# Format code
black src/

# Lint
flake8 src/

# Type checking
mypy src/
```

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Test connection
psql -U postgres -d teams_notetaker -c "SELECT 1;"
```

### Graph API Authentication Errors

```bash
# Verify credentials
python -c "from src.core.config import get_config; print(get_config().graph_api.client_id)"

# Test authentication
python -m src.main health
```

### Worker Not Processing Jobs

```bash
# Check worker status
systemctl --user status teams-notetaker-poller

# View worker logs
journalctl --user -u teams-notetaker-poller -n 100

# Check job queue
psql -U postgres -d teams_notetaker -c "SELECT status, COUNT(*) FROM job_queue GROUP BY status;"
```

### Web Dashboard Not Accessible

```bash
# Check service status
systemctl --user status teams-notetaker-web

# View logs
journalctl --user -u teams-notetaker-web -f

# Test port
curl http://localhost:8000
```

## Azure AD App Registration

See [AZURE_AD.md](docs/AZURE_AD.md) for detailed setup instructions.

**Quick Summary:**
1. Go to Azure Portal > App Registrations > New Registration
2. Add application permissions (not delegated):
   - `OnlineMeetings.Read.All`
   - `OnlineMeetingTranscript.Read.All`
   - `Mail.Send`
   - `Chat.ReadWrite.All`
   - `User.Read.All`
3. Grant admin consent
4. Create client secret
5. Copy client ID, secret, and tenant ID to `.env`

## Monitoring

### Key Metrics

- **Meetings discovered** (per run)
- **Success rate** (completed / total)
- **Processing time** (average per meeting)
- **Token usage** (Claude API costs)
- **Queue depth** (pending jobs)

### Logs

```bash
# Poller logs
tail -f logs/poller.log

# Worker logs
tail -f logs/worker.log

# Web logs
tail -f logs/web.log

# Systemd logs
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f
```

## Security

- **Secrets**: Stored in `.env` file (never committed to git)
- **Authentication**: JWT tokens with 8-hour expiration
- **Session storage**: Database-backed with audit trail
- **OAuth flows**: One-time use with 10-minute expiration
- **Domain validation**: Only @townsquaremedia.com users
- **Input validation**: All user inputs sanitized
- **SQL injection**: Prevented via SQLAlchemy ORM

## Support

**Issues**: https://github.com/yourusername/teams-notetaker/issues

**Developer**: Scott Schatz (scott.schatz@townsquaremedia.com)

**Reference Projects**:
- Invoice Bot: `/home/sschatz/projects/invoice-bot/` (Azure AD SSO patterns)

## License

Proprietary - Townsquare Media

## Roadmap

### Phase 1 ✅ (Current)
- Core infrastructure
- Database and authentication
- Graph API integration

### Phase 2 (In Progress)
- Job queue and worker
- AI summarization
- Distribution system

### Phase 3 (Coming Soon)
- Web dashboard
- Analytics and reporting
- Production deployment

### Future Enhancements
- Real-time WebSocket updates
- Custom summary templates per team
- Meeting recording integration
- Export to CSV/Excel
- Mobile-responsive dashboard
- Multi-language support
- Advanced analytics and insights
