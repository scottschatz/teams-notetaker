# Deployment Guide - Teams Meeting Transcript Summarizer

Complete deployment instructions for WSL2 (Ubuntu) on Windows.

## Prerequisites

### 1. PostgreSQL Database

Install PostgreSQL in WSL:

```bash
# Install PostgreSQL
sudo apt update
sudo apt install postgresql postgresql-contrib

# Start PostgreSQL service
sudo service postgresql start

# Create database user (if needed)
sudo -u postgres createuser -P sschatz  # Enter password when prompted

# Create database
createdb -U sschatz teams_notetaker
```

### 2. Python Environment

```bash
cd /home/sschatz/projects/teams-notetaker

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configuration

#### Create `.env` file:

```bash
cp .env.example .env
nano .env
```

Update with your credentials:
- Graph API credentials (already configured from invoice-bot)
- Database password
- Claude API key (get from https://console.anthropic.com/)
- JWT secret key (generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`)

#### Create `config.yaml`:

```bash
cp config.yaml.example config.yaml
# Edit if needed (defaults are good for most setups)
```

## Database Initialization

```bash
# Initialize database schema
python -m src.main db init

# Seed default configuration
python -m src.main db seed-config

# Verify database
python -m src.main db status
```

## Health Checks

Test all connections:

```bash
python -m src.main health
```

Expected output:
- ✅ Database: Connected
- ✅ Graph API: Connected
- ⚠️  Claude API: Not configured (or ✅ if key is set)

## Deployment Options

### Option 1: Systemd Services (Production)

Install as systemd user services:

```bash
# Install and start services
./deployment/setup-services.sh

# Services will auto-start on boot
```

Manage services:

```bash
# View logs
journalctl --user -u teams-notetaker-poller -f
journalctl --user -u teams-notetaker-web -f

# Restart services
systemctl --user restart teams-notetaker-poller
systemctl --user restart teams-notetaker-web

# Stop services
systemctl --user stop teams-notetaker-{poller,web}

# Check status
systemctl --user status teams-notetaker-poller
systemctl --user status teams-notetaker-web
```

### Option 2: Manual Execution (Development)

Run components separately:

```bash
# Terminal 1: Web dashboard
python -m src.main serve --port 8000

# Terminal 2: Poller + Worker
python -m src.main run --loop
```

Or run both together:

```bash
# Development mode (both in one command)
python -m src.main start-all
```

## Add Pilot Users

```bash
# Add yourself to pilot program
python -m src.main pilot add scott.schatz@townsquaremedia.com --name "Scott Schatz"

# Add other test users
python -m src.main pilot add user@townsquaremedia.com --name "Test User"

# List pilot users
python -m src.main pilot list
```

## Access Dashboard

Open browser: **http://localhost:8000**

### From Windows Host

WSL2 automatically forwards ports to Windows, so you can access from Windows at:
- http://localhost:8000

### From Other Devices on LAN

1. Get WSL IP address:
```bash
ip addr show eth0 | grep inet
```

2. Configure Windows Firewall to allow port 8000

3. Access from other devices:
```
http://<windows-ip>:8000
```

## Testing

### Test Discovery (Dry Run)

```bash
# Discover meetings without processing
python -m src.main run --dry-run
```

### Process Specific Meeting

Once you have meeting IDs from discovery:

```bash
# Process a specific meeting
python -m src.main run --meeting-id <meeting-id>
```

### Monitor Logs

```bash
# View all logs
tail -f logs/*.log

# Or with systemd
journalctl --user -u teams-notetaker-poller -f
```

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL is running
sudo service postgresql status

# Start PostgreSQL
sudo service postgresql start

# Test connection
psql -U postgres -d teams_notetaker -c "SELECT 1"
```

### Graph API Issues

```bash
# Verify credentials
python -m src.main config show

# Test Graph API
python -m src.main health
```

### Worker Not Processing Jobs

```bash
# Check queue status
python -m src.main db status

# View recent jobs
# (Use SQL or implement CLI command)
```

### Web Dashboard Not Accessible

```bash
# Check if service is running
systemctl --user status teams-notetaker-web

# Check port binding
netstat -tlnp | grep 8000

# View logs
journalctl --user -u teams-notetaker-web -n 50
```

## Monitoring

### Queue Statistics

Check job queue health:

```bash
curl http://localhost:8000/api/health/detailed | jq
```

### Dashboard Analytics

Access via web: http://localhost:8000/dashboard

Shows:
- Total meetings processed
- Processing success rate
- Queue depth
- Recent activity

## Switching from Pilot to Production

1. Edit `config.yaml`:
```yaml
pilot_mode_enabled: false
```

2. Restart services:
```bash
systemctl --user restart teams-notetaker-poller
```

3. Monitor logs for increased activity

## Performance Tuning

### Increase Concurrent Jobs

Edit `config.yaml`:
```yaml
max_concurrent_jobs: 10  # Increase from 5
```

### Adjust Polling Interval

Edit `config.yaml`:
```yaml
polling_interval_minutes: 3  # Decrease from 5
```

### Database Optimization

```sql
-- Add indexes if needed
-- Check query performance
EXPLAIN ANALYZE SELECT * FROM meetings WHERE status = 'completed';
```

## Backup and Maintenance

### Database Backup

```bash
# Backup database
pg_dump -U sschatz teams_notetaker > backup_$(date +%Y%m%d).sql

# Restore from backup
psql -U sschatz teams_notetaker < backup_20250101.sql
```

### Clean Old Data

```bash
# TODO: Implement cleanup command
# python -m src.main cleanup --older-than 90d
```

### Update Application

```bash
cd /home/sschatz/projects/teams-notetaker

# Pull latest changes
git pull origin main

# Install new dependencies
source venv/bin/activate
pip install -r requirements.txt

# Restart services
systemctl --user restart teams-notetaker-{poller,web}
```

## Security Notes

- ✅ `.env` file is gitignored (never commit secrets)
- ✅ HTTP-only cookies for sessions
- ✅ JWT tokens with 8-hour expiration
- ✅ Domain validation (@townsquaremedia.com)
- ✅ RBAC (admin/manager/user roles)

## Support

For issues or questions:
- Check logs: `journalctl --user -u teams-notetaker-poller -f`
- Review health: `python -m src.main health`
- GitHub Issues: https://github.com/scottschatz/teams-notetaker/issues
