# Azure Relay Webhook Setup Guide

This guide shows how to receive Microsoft Graph webhooks via Azure Relay Hybrid Connections (no public ports needed!).

## ✅ Prerequisites

- Azure Relay namespace already set up (you mentioned you have this)
- Hybrid Connection created in the namespace
- Graph API permissions: `CallRecords.Read.All` (Application-level)

---

## 🔧 Step 1: Get Azure Relay Connection Info

Since you already have Azure Relay set up for another app, get the connection details:

```bash
# List your Relay namespaces
az relay namespace list --query "[].{Name:name,ResourceGroup:resourceGroup}"

# Get your hybrid connection
az relay hyco list \
  --namespace-name <your-relay-namespace> \
  --resource-group <your-rg>

# Get the connection string
az relay hyco authorization-rule keys list \
  --namespace-name <your-relay-namespace> \
  --hybrid-connection-name <your-hyco-name> \
  --name RootManageSharedAccessKey \
  --resource-group <your-rg>
```

**Example output:**
```json
{
  "primaryKey": "abc123...",
  "secondaryKey": "def456...",
  "primaryConnectionString": "Endpoint=sb://myrelay.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=abc123..."
}
```

---

## 📝 Step 2: Add to .env

Add these to your `.env` file:

```bash
# Azure Relay Configuration
AZURE_RELAY_NAMESPACE=myrelay.servicebus.windows.net
AZURE_RELAY_HYBRID_CONNECTION=teams-webhooks
AZURE_RELAY_KEY_NAME=RootManageSharedAccessKey
AZURE_RELAY_KEY=abc123...your-key-here...

# Webhook public URL (for Graph API subscription)
# This is your Azure Relay HTTPS endpoint
WEBHOOK_BASE_URL=https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks
```

---

## 📊 Step 3: Update config.yaml

Add webhook settings to `config/config.yaml`:

```yaml
webhooks:
  enabled: true
  use_azure_relay: true
  backfill_hours: 48  # How far back to check on startup

app:
  # Change default preference to opt-out
  default_email_preference: false  # Users must explicitly opt-in
```

---

## 🗄️ Step 4: Create Database Table

Run migration to add the `processed_call_records` table:

```bash
# Create the new table
python -m src.main db init
```

---

## 🚀 Step 5: Start Webhook Listener

Start the webhook listener (this connects to Azure Relay):

```bash
# Start worker with webhook listener
python -m src.main run --loop --with-webhooks

# Or run webhook listener separately
python -m src.main webhooks listen
```

**What happens:**
1. Connects to Azure Relay via WebSocket
2. Backfills recent meetings (last 48 hours)
3. Listens for new callRecords webhooks
4. Processes meetings for opted-in users only

---

## 📡 Step 6: Create Microsoft Graph Subscription

Create the webhook subscription pointing to your Azure Relay endpoint:

```bash
# Use CLI helper (will be added)
python -m src.main webhooks subscribe

# Or manually via Graph API:
POST https://graph.microsoft.com/v1.0/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "changeType": "created",
  "notificationUrl": "https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks/callrecords",
  "resource": "/communications/callRecords",
  "expirationDateTime": "2025-12-24T11:00:00.0000000Z",
  "clientState": "secretClientValue"
}
```

**Response:**
```json
{
  "id": "subscription-id-here",
  "resource": "/communications/callRecords",
  "changeType": "created",
  "clientState": "secretClientValue",
  "notificationUrl": "https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks/callrecords",
  "expirationDateTime": "2025-12-24T11:00:00Z"
}
```

**Save the subscription ID!** You'll need it to renew the subscription.

---

## ✅ Step 7: Test the Setup

### Test 1: Validation

Microsoft Graph will send a validation request when you create the subscription. Your listener should automatically respond.

**Expected log output:**
```
2025-12-17 11:30:00 - INFO - ✅ Connected to Azure Relay
2025-12-17 11:30:05 - INFO - Responding to Microsoft Graph validation request
```

### Test 2: Backfill

Check that recent meetings were discovered:

```bash
# Check database for recently discovered meetings
python -m src.main db status

# Should see meetings from last 48 hours
```

### Test 3: Real-time Webhook

1. Have a Teams meeting with transcription enabled
2. End the meeting
3. Wait 2-5 minutes for Microsoft to process
4. Check logs for webhook notification

**Expected log output:**
```
2025-12-17 11:45:00 - INFO - Received callRecords notification: created
2025-12-17 11:45:01 - INFO - Meeting has 3 opted-in participants
2025-12-17 11:45:01 - INFO - ✅ Enqueued fetch_transcript job for meeting 183
```

---

## 🔄 Step 8: Opt Users In

Since default is now opt-out, users must explicitly opt-in:

### Option A: Database Insert (for initial users)

```sql
-- Opt yourself in
INSERT INTO user_preferences (user_email, receive_emails, email_preference)
VALUES ('sschatz@townsquaremedia.com', true, 'all');

-- Opt in other pilot users
INSERT INTO user_preferences (user_email, receive_emails, email_preference)
VALUES
  ('edwin.lovett@townsquaremedia.com', true, 'all'),
  ('joe.ainsworth@townsquaremedia.com', true, 'all');
```

### Option B: Chat Command (once system is running)

Users type in any meeting chat:
```
@meeting notetaker enable emails
```

### Option C: Web Dashboard (future)

Add an opt-in page to your dashboard.

---

## 📋 Architecture Flow

```
┌─────────────────────────────────────────┐
│  Microsoft Graph                        │
│  Meeting ends → callRecord created      │
└─────────────────────────────────────────┘
                 │
                 │ HTTPS POST (webhook)
                 ↓
┌─────────────────────────────────────────┐
│  Azure Relay Hybrid Connection          │
│  Public HTTPS endpoint (no firewall)    │
└─────────────────────────────────────────┘
                 │
                 │ WebSocket (secure tunnel)
                 ↓
┌─────────────────────────────────────────┐
│  WSL - Azure Relay Listener             │
│  - Receives webhook via WebSocket       │
│  - Checks opt-in users                  │
│  - Enqueues jobs if ≥1 opted-in         │
└─────────────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────┐
│  PostgreSQL (WSL)                       │
│  - processed_call_records (dedupe)      │
│  - meetings, job_queue, etc             │
└─────────────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────┐
│  Job Worker (WSL)                       │
│  - fetch_transcript                     │
│  - generate_summary (single-call!)      │
│  - distribute (opted-in users only)     │
└─────────────────────────────────────────┘
```

---

## 🔍 Troubleshooting

### Webhook not receiving notifications

1. **Check Azure Relay connection:**
   ```bash
   # Check listener logs
   tail -f /tmp/teams-notetaker-worker.log | grep "Azure Relay"

   # Should see: "✅ Connected to Azure Relay"
   ```

2. **Verify subscription is active:**
   ```bash
   GET https://graph.microsoft.com/v1.0/subscriptions/<subscription-id>
   ```

3. **Check subscription expiry:**
   Subscriptions expire after ~180 days. You'll need to renew them.

### Meetings not being processed

1. **Check if users are opted-in:**
   ```sql
   SELECT * FROM user_preferences;
   ```

2. **Check if transcription was enabled:**
   ```bash
   # Check processed_call_records for skipped meetings
   SELECT * FROM processed_call_records WHERE source = 'webhook';
   ```

3. **Check job queue:**
   ```sql
   SELECT * FROM job_queue WHERE created_at > NOW() - INTERVAL '1 hour';
   ```

### "AI Updates" meeting still missing email

The meeting from this morning is still stuck because:
- Transcript shows in Teams UI but not via Graph API
- This is a Microsoft lag issue (sometimes 20-30 min)
- Once webhooks are set up, future meetings will be caught in real-time

**To manually process it once transcript is available:**
```bash
# Check if transcript is available now
python -m src.main meetings check-transcript --meeting-id 182

# Manually enqueue if available
python -m src.main meetings process --meeting-id 182
```

---

## 🎯 Benefits of This Setup

✅ **No public ports** - Everything stays in WSL
✅ **Enterprise-ready** - Uses your existing Azure Relay
✅ **Org-wide discovery** - One webhook for entire company
✅ **Efficient** - No polling 2,500 calendars
✅ **Opt-in system** - Users explicitly enable
✅ **No duplicates** - 1,000-person meeting = 1 notification
✅ **Secure** - Azure Relay provides encrypted tunnel

---

## 📞 Next Steps

1. ✅ Add Azure Relay credentials to `.env`
2. ✅ Start webhook listener
3. ✅ Create Graph API subscription
4. ✅ Opt yourself in to test
5. ✅ Have a test meeting
6. ✅ Verify email arrives!

Once working, you can:
- Add more users to opt-in list
- Monitor via dashboard
- Scale to full organization
