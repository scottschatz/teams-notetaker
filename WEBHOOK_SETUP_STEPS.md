# Azure Relay Webhook Setup - Step-by-Step Guide

## Part 1: Get Azure Relay Credentials (Azure Portal)

### Step 1: Find Your Azure Relay Namespace

1. Go to **https://portal.azure.com**
2. In the search bar at top, type: `relay`
3. Click **Relay** (under Services)
4. You should see your existing Relay namespace listed

**If you see your namespace**: Click on it and continue to Step 2

**If you DON'T see a namespace**:
- You'll need to create one (or it might be in a different subscription)
- Check the subscription dropdown at the top
- Or create a new one: Click **+ Create** → Resource Group, Name (e.g., `teams-webhook-relay`), Region

---

### Step 2: Create a Hybrid Connection

Once you're in your Relay namespace:

1. In the left menu, click **Hybrid Connections**
2. Click **+ Hybrid Connection** at the top
3. Fill in:
   - **Name**: `teams-webhooks` (or any name you prefer)
   - **Requires Client Authorization**: Check this box ✅
4. Click **Create**

**Screenshot location**: Left menu → Hybrid Connections → + Hybrid Connection button

---

### Step 3: Get the Connection String

1. Click on your new `teams-webhooks` hybrid connection
2. In the left menu, click **Shared access policies**
3. Click **RootManageSharedAccessKey** (it should be there by default)
4. You'll see:
   - **Primary Key** - A long string like `abc123...` - **COPY THIS**
   - **Primary Connection String** - **COPY THIS TOO** (for reference)

**What to copy**:
- The **namespace** part from connection string (e.g., `myrelay.servicebus.windows.net`)
- The **Primary Key** (the long random string)

**Example Connection String**:
```
Endpoint=sb://myrelay.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=abc123def456...
```

From this example:
- **Namespace**: `myrelay.servicebus.windows.net`
- **Key**: `abc123def456...`

---

## Part 2: Add to Your .env File (WSL)

Open your `.env` file in the teams-notetaker directory:

```bash
cd ~/projects/teams-notetaker
nano .env
```

Add these lines (replace with YOUR values):

```bash
# Azure Relay Configuration (for webhooks)
AZURE_RELAY_NAMESPACE=myrelay.servicebus.windows.net
AZURE_RELAY_HYBRID_CONNECTION=teams-webhooks
AZURE_RELAY_KEY_NAME=RootManageSharedAccessKey
AZURE_RELAY_KEY=abc123def456...your-actual-key-here...
```

Save and exit (`Ctrl+X`, then `Y`, then `Enter`)

---

## Part 3: Test the Configuration

Run the test command:

```bash
source venv/bin/activate
python -m src.main webhooks test
```

**Expected output**:
```
🧪 Testing Azure Relay Setup
================================================================================

1. Checking configuration...
   ✅ Azure Relay configured
      Namespace: myrelay.servicebus.windows.net
      Hybrid Connection: teams-webhooks
      Webhook URL: https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks

2. Checking database...
   ✅ Database connected
      Processed call records: 0

3. Checking Graph API...
   ✅ Graph API connected

================================================================================
✅ All checks passed!
```

**If you see errors**:
- Check that you copied the key correctly (no extra spaces)
- Make sure the namespace includes `.servicebus.windows.net`

---

## Part 4: Create the Database Table

The webhook system needs a new table to track processed meetings:

```bash
source venv/bin/activate
python -m src.main db init
```

This will create the `processed_call_records` table.

---

## Part 5: Start the Webhook Listener

Now you can start the webhook listener:

```bash
source venv/bin/activate
python -m src.main webhooks listen --backfill
```

**Expected output**:
```
🔐 Azure Relay Webhook Listener
================================================================================
Namespace: myrelay.servicebus.windows.net
Hybrid Connection: teams-webhooks
Webhook URL: https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks/callrecords
================================================================================

📊 Backfilling meetings from last 48 hours...
✅ Backfill complete

🎧 Starting listener...
   Press Ctrl+C to stop

✅ Connected to Azure Relay
Listening for webhooks...
```

**Leave this running** - This process will:
1. Backfill recent meetings (last 48 hours)
2. Listen for new meeting notifications in real-time

---

## Part 6: Create Microsoft Graph Subscription

In a **NEW terminal** (keep the listener running):

```bash
cd ~/projects/teams-notetaker
source venv/bin/activate
python -m src.main webhooks subscribe --expiration-days 180
```

**Expected output**:
```
📡 Creating Microsoft Graph Subscription
================================================================================
Resource: /communications/callRecords
Notification URL: https://myrelay.servicebus.windows.net:443/$hc/teams-webhooks/callrecords
Expiration: 2026-06-16 11:00:00 UTC

Creating subscription...

✅ Subscription created successfully!
================================================================================
Subscription ID: abc-123-def-456
Expires: 2026-06-16T11:00:00Z

💾 Save this subscription ID to renew it before expiration:
   python -m src.main webhooks renew --subscription-id abc-123-def-456
```

**IMPORTANT**: Save that subscription ID! You'll need it to renew before expiration.

---

## Part 7: Opt Yourself In

Since the webhook system uses opt-in by default, you need to opt yourself in:

```bash
source venv/bin/activate
python -m src.main db shell
```

Then run this SQL (replace with your email):

```sql
INSERT INTO user_preferences (user_email, receive_emails, email_preference)
VALUES ('Scott.Schatz@townsquaremedia.com', true, 'all')
ON CONFLICT (user_email) DO UPDATE SET receive_emails = true;
```

Exit the shell:
```sql
\q
```

---

## Part 8: Test with a Real Meeting

1. Have a Teams meeting with transcription enabled
2. End the meeting
3. Wait 2-5 minutes for Microsoft to process the transcript
4. Check the webhook listener logs - you should see:

```
Received callRecords notification: created
Meeting has 1 opted-in participants
✅ Enqueued fetch_transcript job for meeting 183
```

5. The summary email will arrive shortly after!

---

## Troubleshooting

### "Azure Relay not configured"
- Check `.env` file has all 4 variables set
- Make sure there are no quotes around the values
- Restart any running processes after changing `.env`

### "Failed to create subscription: 400 error"
- Make sure the webhook listener is running FIRST
- Check that your Graph API app has `CallRecords.Read.All` permission
- Verify the permission is **admin-consented** in Azure AD

### "No meetings discovered via webhook"
- Check that you've opted in (user_preferences table)
- Verify subscription is active: `python -m src.main webhooks list`
- Make sure transcription was enabled during the meeting

### Connection drops/errors
- Azure Relay will auto-reconnect on network issues
- If it keeps failing, check the namespace and key are correct
- Verify firewall isn't blocking WebSocket connections

---

## Running Both Polling AND Webhooks

Good news: They work together!

- **Polling**: Continues every 5 minutes (safety net for missed meetings)
- **Webhooks**: Real-time notifications (instant discovery)

The `processed_call_records` table prevents duplicates, so even if both systems discover the same meeting, it will only be processed once.

---

## Next Steps After Setup

1. **Monitor the first few meetings**: Check logs to ensure webhooks are working
2. **Add more users**: Insert them into `user_preferences` table to opt them in
3. **Set up renewal reminder**: Subscriptions expire after 180 days, set a calendar reminder to renew

---

## Commands Reference

```bash
# Test configuration
python -m src.main webhooks test

# Start listener (with backfill)
python -m src.main webhooks listen --backfill

# Start listener (no backfill)
python -m src.main webhooks listen --no-backfill

# Create subscription
python -m src.main webhooks subscribe --expiration-days 180

# List active subscriptions
python -m src.main webhooks list

# Delete a subscription
python -m src.main webhooks delete <subscription-id>
```

---

## Questions?

If you run into issues:
1. Check the listener logs for error messages
2. Run `python -m src.main webhooks test` to verify config
3. Verify subscription is active with `python -m src.main webhooks list`
