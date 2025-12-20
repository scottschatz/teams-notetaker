# Deployment Guide - Teams Meeting Transcript Summarizer

Complete deployment instructions for WSL2 (Ubuntu) on Windows.

**Last Updated**: 2025-12-19
**Status**: Production Ready

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Configuration](#configuration)
4. [Database Initialization](#database-initialization)
5. [Service Deployment](#service-deployment)
6. [Subscriber Management](#subscriber-management)
7. [Webhook Setup](#webhook-setup)
8. [Testing](#testing)
9. [Monitoring](#monitoring)
10. [Troubleshooting](#troubleshooting)
11. [Updating](#updating)

---

## Prerequisites

### 1. System Requirements

- **WSL2** with Ubuntu 20.04+ (or native Linux)
- **Python 3.11+**
- **PostgreSQL 12+**
- **systemd** enabled in WSL (for auto-start services)

### 2. Azure AD Application

You need an Azure AD app registration with the following **Application permissions**:

- `CallRecords.Read.All` - Read call records
- `OnlineMeetings.Read.All` - Read Teams meeting metadata
- `OnlineMeetingTranscript.Read.All` - Read meeting transcripts
- `Chat.Read.All` - Read meeting chat (for event detection)
- `Mail.Send` - Send summary emails
- `Mail.Read` - Read inbox for subscribe/unsubscribe
- `User.Read.All` - Read user profiles and photos

**Admin consent required** for all permissions.

See [PERMISSIONS_SETUP.md](PERMISSIONS_SETUP.md) for detailed instructions.

### 3. Azure Relay (for Webhooks)

- Azure Relay namespace created
- Hybrid Connection configured
- Shared Access Key obtained

See [AZURE_RELAY_SETUP.md](AZURE_RELAY_SETUP.md) for setup instructions.

### 4. Claude API Key

- Sign up at https://console.anthropic.com/
- Create an API key
- Ensure sufficient credits for usage

---

## Initial Setup

### 1. Install PostgreSQL

```bash
# Install PostgreSQL
sudo apt update
sudo apt install postgresql postgresql-contrib

# Start PostgreSQL service
sudo service postgresql start

# Auto-start on WSL boot (add to ~/.bashrc or /etc/rc.local)
echo "sudo service postgresql start" >> ~/.bashrc
```

### 2. Create Database User and Database

```bash
# Switch to postgres user
sudo -u postgres psql

# In psql shell:
CREATE USER your_username WITH PASSWORD 'your_password';
CREATE DATABASE teams_notetaker OWNER your_username;
GRANT ALL PRIVILEGES ON DATABASE teams_notetaker TO your_username;
\q

# Test connection
psql -U your_username -d teams_notetaker -c "SELECT 1"
```

### 3. Clone Repository

```bash
cd ~/projects
git clone https://github.com/scottschatz/teams-notetaker.git
cd teams-notetaker
```

### 4. Set Up Python Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

### 1. Create Environment File

```bash
# Copy example
cp .env.example .env

# Edit with your credentials
nano .env
```

### 2. Configure .env File

```bash
# Microsoft Graph API
GRAPH_CLIENT_ID=your-app-id
GRAPH_CLIENT_SECRET=your-secret
GRAPH_TENANT_ID=your-tenant-id

# Claude API
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Database
DATABASE_URL=postgresql://your_username:your_password@localhost/teams_notetaker

# Azure Relay (Webhooks)
AZURE_RELAY_NAMESPACE=yournamespace.servicebus.windows.net
AZURE_RELAY_HYBRID_CONNECTION=teams-webhooks
AZURE_RELAY_KEY_NAME=RootManageSharedAccessKey
AZURE_RELAY_KEY=your-relay-shared-access-key

# Web Dashboard
JWT_SECRET_KEY=generate-with-python-secrets-token-urlsafe-32

# Email Configuration
SMTP_SERVER=smtp.office365.com
SMTP_PORT=587
SMTP_USERNAME=note.taker@yourdomain.com
SMTP_PASSWORD=your-app-password
```

### 3. Generate JWT Secret

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Copy output to JWT_SECRET_KEY in .env
```

### 4. Configure Application Settings

```bash
# Copy example config (if not already present)
cp config.yaml.example config.yaml

# Edit settings
nano config.yaml
```

**Key settings to review**:
```yaml
# Email from address (must match inbox monitoring email)
email_from: "note.taker@yourdomain.com"

# Claude model
claude_model: "claude-haiku-4-5"  # Cost-optimized

# Job processing
max_concurrent_jobs: 5
job_timeout_minutes: 10

# Inbox monitoring
inbox_check_interval_seconds: 60
inbox_lookback_minutes: 60
```

### 5. Set File Permissions

```bash
# Secure .env file
chmod 600 .env

# Ensure config is readable
chmod 644 config.yaml
```

---

## Database Initialization

### 1. Initialize Database Schema

```bash
# Activate virtual environment
source venv/bin/activate

# Initialize database
python -m src.main db init
```

This creates all necessary tables:
- `meetings`, `meeting_participants`
- `transcripts`, `summaries`, `distributions`
- `job_queue`
- `meeting_subscribers`, `email_aliases`
- `processed_call_records`, `processed_inbox_items`
- Supporting tables (pilot_users, app_config, etc.)

### 2. Verify Database

```bash
# Check database health
python -m src.main db health

# Expected output:
# ✅ Database: Connected
# Tables: 15 created
```

### 3. Inspect Tables (Optional)

```bash
# Connect to database
psql -U your_username -d teams_notetaker

# List tables
\dt

# View schema
\d meetings
\d meeting_subscribers

# Exit
\q
```

---

## Service Deployment

### 1. Enable systemd in WSL

Edit `/etc/wsl.conf`:
```bash
sudo nano /etc/wsl.conf
```

Add:
```ini
[boot]
systemd=true
```

Restart WSL:
```bash
# From Windows PowerShell
wsl --shutdown
# Wait 5 seconds
wsl
```

### 2. Enable User Linger

Allows services to run when you're not logged in:
```bash
sudo loginctl enable-linger $USER
```

### 3. Deploy Service Files

```bash
# Run deployment script
./scripts/deploy_services.sh
```

This creates and enables:
- `teams-notetaker-poller.service` - Worker + backfill
- `teams-notetaker-web.service` - Web dashboard

Or manually:

```bash
# Create service directory
mkdir -p ~/.config/systemd/user

# Copy service files
cp deployment/teams-notetaker-poller.service ~/.config/systemd/user/
cp deployment/teams-notetaker-web.service ~/.config/systemd/user/

# Reload systemd
systemctl --user daemon-reload

# Enable services (auto-start)
systemctl --user enable teams-notetaker-poller
systemctl --user enable teams-notetaker-web

# Start services
systemctl --user start teams-notetaker-poller
systemctl --user start teams-notetaker-web
```

### 4. Verify Services

```bash
# Check status
systemctl --user status teams-notetaker-poller
systemctl --user status teams-notetaker-web

# View logs
journalctl --user -u teams-notetaker-poller -n 50
journalctl --user -u teams-notetaker-web -n 50
```

### 5. Service Management Commands

```bash
# Restart services
systemctl --user restart teams-notetaker-poller teams-notetaker-web

# Stop services
systemctl --user stop teams-notetaker-poller teams-notetaker-web

# View logs in real-time
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f

# Show service status
systemctl --user list-units teams-notetaker*
```

---

## Subscriber Management

### 1. Initial Subscribers

Add yourself as first subscriber:

```bash
# Via CLI
python -m src.main subscribers add your.email@company.com --name "Your Name"

# Via SQL
psql -U your_username -d teams_notetaker -c \
  "INSERT INTO meeting_subscribers (email, display_name, is_subscribed) \
   VALUES ('your.email@company.com', 'Your Name', true);"
```

### 2. Add More Subscribers

**Via CLI**:
```bash
python -m src.main subscribers add email@company.com --name "Name"
```

**Via Web Dashboard**:
1. Navigate to http://localhost:8000/admin/users
2. Click "Add Subscriber"
3. Enter email and name
4. Click "Add"

**Via Email** (once inbox monitoring is running):
- Users send email to note.taker@yourdomain.com with subject "subscribe"
- System automatically adds them and sends confirmation

### 3. List Subscribers

```bash
python -m src.main subscribers list

# Or via SQL
psql -U your_username -d teams_notetaker -c \
  "SELECT email, display_name, is_subscribed, created_at \
   FROM meeting_subscribers \
   ORDER BY created_at DESC;"
```

---

## Webhook Setup

### 1. Start Webhook Listener

Webhook listener is included in `teams-notetaker-poller` service, but can also run standalone:

```bash
# Check if running
systemctl --user status teams-notetaker-poller | grep "Azure Relay"

# Or start manually (for testing)
python -m src.main webhooks listen
```

### 2. Create Webhook Subscription

```bash
# Create callRecords subscription
python -m src.main webhooks subscribe-callrecords

# Or create callTranscripts subscription (recommended)
python -m src.main webhooks subscribe-transcripts
```

**Note**: Subscriptions expire after 60 minutes by default. The system automatically renews them.

### 3. Verify Webhook

```bash
# Check webhook status
python -m src.main webhooks status

# List active subscriptions
python -m src.main webhooks list
```

See [WEBHOOK_IMPLEMENTATION.md](WEBHOOK_IMPLEMENTATION.md) for detailed webhook setup.

---

## Testing

### 1. Run Health Checks

```bash
# Check all API connections
python -m src.main health

# Expected output:
# ✅ Database: Connected
# ✅ Graph API: Connected
# ✅ Claude API: Connected
# ✅ Azure Relay: Connected
```

### 2. Test Meeting Discovery (Dry Run)

```bash
# Discover meetings without processing
python -m src.main run --dry-run

# Check what would be discovered
```

### 3. Force Backfill

```bash
# Backfill last 24 hours
python -m src.main backfill --hours 24

# Or via web dashboard:
# http://localhost:8000/diagnostics → Force Backfill
```

### 4. Check Job Queue

```bash
# View queue statistics
curl http://localhost:8000/api/stats | jq

# Or via SQL
psql -U your_username -d teams_notetaker -c \
  "SELECT job_type, status, COUNT(*) \
   FROM job_queue \
   GROUP BY job_type, status;"
```

### 5. Test Email Distribution

```bash
# Send test email
python -m src.main test-email your.email@company.com

# Or via web dashboard:
# http://localhost:8000/diagnostics → Send Test Email
```

### 6. Access Web Dashboard

1. Open browser: http://localhost:8000
2. Login with your credentials
3. Navigate to:
   - **Meetings** - View processed meetings
   - **Diagnostics** - System health and controls
   - **Admin > Users** - Subscriber management

---

## Monitoring

### 1. Service Health

```bash
# Check service status
systemctl --user status teams-notetaker-poller
systemctl --user status teams-notetaker-web

# Check if auto-start is enabled
systemctl --user is-enabled teams-notetaker-poller
systemctl --user is-enabled teams-notetaker-web
```

### 2. Log Monitoring

```bash
# Follow poller logs
journalctl --user -u teams-notetaker-poller -f

# Follow web logs
journalctl --user -u teams-notetaker-web -f

# Show last 100 lines
journalctl --user -u teams-notetaker-poller -n 100

# Filter for errors
journalctl --user -u teams-notetaker-poller | grep ERROR

# Filter for specific meeting
journalctl --user -u teams-notetaker-poller | grep "Meeting 123"
```

### 3. Database Monitoring

```sql
-- Recent job status
SELECT job_type, status, COUNT(*)
FROM job_queue
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY job_type, status;

-- Subscriber count
SELECT COUNT(*) FROM meeting_subscribers WHERE is_subscribed = true;

-- Recent meetings
SELECT id, subject, start_time, status, has_summary
FROM meetings
WHERE start_time > NOW() - INTERVAL '7 days'
ORDER BY start_time DESC
LIMIT 20;

-- Failed jobs
SELECT id, job_type, error, created_at
FROM job_queue
WHERE status = 'failed'
ORDER BY created_at DESC
LIMIT 10;
```

### 4. API Health

```bash
# Basic health
curl http://localhost:8000/health

# Deep health (database + APIs)
curl http://localhost:8000/health/deep | jq

# Queue statistics
curl http://localhost:8000/api/stats | jq
```

### 5. Metrics to Watch

- **Job queue depth**: Should stay <20 pending jobs
- **Job failure rate**: Should be <10%
- **Service uptime**: Should be 100% (auto-restart on failure)
- **Subscriber growth**: Track over time
- **API errors**: Should be minimal

---

## Troubleshooting

### Services Not Starting

```bash
# Check service status
systemctl --user status teams-notetaker-poller

# View full logs
journalctl --user -u teams-notetaker-poller -n 200

# Check for Python errors
journalctl --user -u teams-notetaker-poller | grep -i "error\|exception\|traceback"

# Clear Python cache and restart
find . -type d -name __pycache__ -exec rm -rf {} +
find . -name "*.pyc" -delete
systemctl --user restart teams-notetaker-poller
```

### Database Connection Issues

```bash
# Check PostgreSQL is running
sudo service postgresql status

# Start PostgreSQL
sudo service postgresql start

# Test connection
psql -U your_username -d teams_notetaker -c "SELECT 1"

# Check DATABASE_URL in .env
cat .env | grep DATABASE_URL
```

### Jobs Stuck in Queue

System automatically recovers stale jobs every 60 seconds. Check recovery logs:

```bash
journalctl --user -u teams-notetaker-poller | grep -i "recovered\|orphaned"
```

Manual recovery:
```sql
UPDATE job_queue SET status = 'pending', worker_id = NULL
WHERE status = 'running' AND heartbeat_at < NOW() - INTERVAL '15 minutes';
```

### Webhooks Not Working

```bash
# Check Azure Relay connection
journalctl --user -u teams-notetaker-poller | grep "Azure Relay"

# Verify subscription exists
python -m src.main webhooks list

# Check subscription expiry
python -m src.main webhooks status

# Renew subscriptions
python -m src.main webhooks renew-all
```

### Emails Not Sending

**Check subscriber status**:
```sql
SELECT email, is_subscribed FROM meeting_subscribers WHERE email = 'user@company.com';
```

**Check inbox monitoring**:
```bash
journalctl --user -u teams-notetaker-poller | grep "inbox"
```

**Check email configuration**:
```bash
# Verify .env has correct SMTP settings
cat .env | grep SMTP

# Test email sending
python -m src.main test-email your.email@company.com
```

### Web Dashboard Not Accessible

```bash
# Check service
systemctl --user status teams-notetaker-web

# Check port binding
netstat -tlnp | grep 8000

# View web logs
journalctl --user -u teams-notetaker-web -n 50

# Test direct access
curl http://localhost:8000/health
```

---

## Updating

### Updating Code

```bash
cd ~/projects/teams-notetaker

# Pull latest changes
git pull origin main

# Activate virtual environment
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
find . -name "*.pyc" -delete

# Restart services
systemctl --user restart teams-notetaker-poller teams-notetaker-web

# Check logs
journalctl --user -u teams-notetaker-poller -f
```

### Database Migrations

If database schema changes:

```bash
# Check for migration files
ls migrations/

# Run migrations manually
psql -U your_username -d teams_notetaker -f migrations/migration_file.sql

# Or use CLI (if implemented)
python -m src.main db migrate
```

### Configuration Updates

```bash
# Backup current config
cp config.yaml config.yaml.backup

# Edit config
nano config.yaml

# Restart services
systemctl --user restart teams-notetaker-poller teams-notetaker-web
```

---

## Performance Tuning

### Increase Concurrent Jobs

Edit `config.yaml`:
```yaml
max_concurrent_jobs: 10  # Increase from 5
```

Restart services:
```bash
systemctl --user restart teams-notetaker-poller
```

### Adjust Polling Interval

Edit `config.yaml`:
```yaml
polling_interval_minutes: 3  # Decrease from 5 for faster discovery
```

### Database Optimization

```sql
-- Add indexes if needed
CREATE INDEX idx_meetings_start_time ON meetings(start_time);
CREATE INDEX idx_job_queue_status_priority ON job_queue(status, priority DESC, created_at);

-- Analyze query performance
EXPLAIN ANALYZE SELECT * FROM meetings WHERE status = 'completed';

-- Vacuum database
VACUUM ANALYZE;
```

---

## Backup and Maintenance

### Database Backup

```bash
# Backup database
pg_dump -U your_username teams_notetaker > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup with compression
pg_dump -U your_username teams_notetaker | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Restore from backup
psql -U your_username teams_notetaker < backup_20251219_120000.sql
```

### Clean Old Data

```sql
-- Archive old meetings (>90 days)
DELETE FROM meetings WHERE start_time < NOW() - INTERVAL '90 days';

-- Clean completed jobs (>30 days)
DELETE FROM job_queue WHERE status = 'completed' AND completed_at < NOW() - INTERVAL '30 days';

-- Vacuum to reclaim space
VACUUM ANALYZE;
```

### Log Rotation

systemd handles log rotation automatically, but you can configure it:

```bash
# Configure journald log size
sudo nano /etc/systemd/journald.conf

# Set limits
SystemMaxUse=500M
SystemKeepFree=1G
SystemMaxFileSize=50M
```

---

## Security Checklist

- [ ] .env file has 600 permissions
- [ ] No credentials in config.yaml
- [ ] PostgreSQL only accessible from localhost
- [ ] Web dashboard requires authentication
- [ ] Azure AD admin consent granted
- [ ] Azure Relay shared access key secured
- [ ] JWT secret is random and secure
- [ ] Services run as non-root user
- [ ] firewall configured (if applicable)

---

## Production Checklist

Before going live:

- [ ] All health checks passing
- [ ] Services auto-start on WSL boot
- [ ] Webhook subscription active and renewing
- [ ] Inbox monitoring working
- [ ] Test subscriber added and receiving emails
- [ ] Backfill completed for recent meetings
- [ ] Web dashboard accessible
- [ ] Monitoring and alerting configured
- [ ] Backup script scheduled
- [ ] Documentation reviewed and updated

---

## Support

For issues or questions:
- Check logs: `journalctl --user -u teams-notetaker-poller -f`
- Review health: `python -m src.main health`
- Consult [TROUBLESHOOTING](#troubleshooting) section above
- Check [CLAUDE.md](CLAUDE.md) for development guidance
- GitHub Issues: https://github.com/scottschatz/teams-notetaker/issues

---

**Last Updated**: 2025-12-19
**Version**: 3.0
**Status**: Production Ready
