# Graph API Permissions Setup Guide

**Status**: ⚠️ Permissions needed to access user calendars and Teams meetings

## Current Issue

The application is getting **403 Forbidden** when trying to access user calendars. This is because the Azure AD app registration doesn't have the necessary permissions.

## App Registration Details

- **App ID**: `a55e7e21-9850-4453-9a09-7ce7b6347b49`
- **Tenant**: TowneSquare Media (`a473edd8-ba25-4f04-a0a8-e8ad25c19632`)
- **Current Name**: SharePoint Access (from invoice-bot)

## Required Permissions

The following **Application permissions** are needed (not Delegated permissions):

### ✅ Already Have
- `Sites.Read.All` - SharePoint sites
- `Files.Read.All` - SharePoint files

### ❌ Need to Add

1. **Calendars.Read** - Read user calendars to discover meetings
2. **OnlineMeetings.Read.All** - Read Teams meeting details
3. **OnlineMeetingTranscript.Read.All** - Read meeting transcripts
4. **User.Read.All** - Read user profile information (for participant details)

## Step-by-Step Setup

### Option 1: Add Permissions to Existing App (Recommended if you own it)

1. **Open Azure Portal**
   - Go to: https://portal.azure.com
   - Navigate to: **Azure Active Directory** → **App registrations**

2. **Find Your App**
   - Search for app ID: `a55e7e21-9850-4453-9a09-7ce7b6347b49`
   - Or search by name: "SharePoint Access"

3. **Add Permissions**
   - Click: **API permissions** (left sidebar)
   - Click: **Add a permission**
   - Select: **Microsoft Graph** → **Application permissions**
   - Search and add each permission:
     - ☐ `Calendars.Read`
     - ☐ `OnlineMeetings.Read.All`
     - ☐ `OnlineMeetingTranscript.Read.All`
     - ☐ `User.Read.All`

4. **Grant Admin Consent**
   - Click: **Grant admin consent for [Your Organization]**
   - Requires: Global Administrator or Application Administrator role
   - Confirm the consent

5. **Wait for Propagation**
   - Permissions typically take 5-10 minutes to propagate
   - In some cases may take up to 1 hour

### Option 2: Create New App Registration (If you want separation)

If you want to keep the SharePoint app separate from the Teams app:

1. **Create New App**
   - Azure AD → App registrations → New registration
   - Name: "Teams Meeting Transcript Summarizer"
   - Supported account types: Single tenant
   - Redirect URI: (leave blank for now)

2. **Create Client Secret**
   - After creation, go to: Certificates & secrets
   - New client secret
   - Description: "Teams Notetaker App"
   - Expires: 24 months (or your preference)
   - **Copy the secret value** (you won't see it again!)

3. **Add API Permissions**
   - API permissions → Add a permission
   - Microsoft Graph → Application permissions
   - Add all 4 permissions listed above
   - Grant admin consent

4. **Update .env File**
   ```bash
   # Replace with new app credentials
   GRAPH_CLIENT_ID=<new-app-id>
   GRAPH_CLIENT_SECRET=<new-client-secret>
   GRAPH_TENANT_ID=a473edd8-ba25-4f04-a0a8-e8ad25c19632
   ```

5. **Restart Services**
   ```bash
   systemctl --user restart teams-notetaker-poller teams-notetaker-web
   ```

## Testing After Setup

Once permissions are added and admin consent is granted:

```bash
# Test discovery (dry run)
python -m src.main run --dry-run

# Expected output:
# - Should show "Discovering meetings for 2 pilot users"
# - Should NOT show 403 errors
# - Should show actual meetings if any exist in last 48 hours
```

## Troubleshooting

### Still Getting 403 After Adding Permissions

1. **Wait Longer**: Permissions can take up to 1 hour to propagate
2. **Check Admin Consent**: Verify the consent was actually granted (green checkmarks)
3. **Token Cache**: The app might be using a cached token without new permissions
   - Restart services: `systemctl --user restart teams-notetaker-poller`
   - Or wait for token to expire (1 hour)

### Cannot Grant Admin Consent

- **Required Role**: Global Administrator or Application Administrator
- **Contact**: Your IT admin or Azure AD admin
- **Alternative**: Use Delegated permissions instead (requires user login flow)

### How to Verify Permissions

1. Azure Portal → App registrations → Your app
2. API permissions tab
3. Look for green checkmarks under "Status" column
4. Should say "Granted for [Your Organization]"

## Expected Behavior After Setup

Once permissions are working:

1. **Discovery**: System will poll every 5 minutes
2. **Query**: Checks calendars of all active pilot users
3. **Filter**: Only Teams meetings (isOnlineMeeting=true)
4. **Range**: Last 48 hours (configurable in config.yaml)
5. **Process**: Meetings with transcripts → Claude summary → Email + Teams chat

## Monitoring

```bash
# Watch discovery logs in real-time
journalctl --user -u teams-notetaker-poller -f

# Look for these log messages:
# ✅ "Discovering meetings for N pilot users"
# ✅ "Discovered N meetings from Graph API"
# ❌ "403 - Access is denied" (means permissions not yet working)
```

## Security Notes

- **Application Permissions**: App can access ALL users' data (not just signed-in user)
- **Scope**: Limited to TowneSquare Media tenant only
- **Audit**: All access is logged in Azure AD audit logs
- **Principle of Least Privilege**: Only request permissions actually needed

## Questions?

- **Azure AD Help**: https://docs.microsoft.com/en-us/azure/active-directory/
- **Graph API Permissions**: https://docs.microsoft.com/en-us/graph/permissions-reference
- **This Project**: See DEPLOYMENT.md or HANDOVER.md
