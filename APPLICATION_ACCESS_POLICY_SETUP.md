# Application Access Policy Setup for Teams Meeting Transcript Access

## Purpose
This document explains why and how to configure an Application Access Policy (AAP) to allow our Teams Meeting Transcript Summarizer application to access meeting transcripts via Microsoft Graph API.

## Background

We've built an internal application that:
1. Automatically discovers Teams meetings across the organization
2. Fetches meeting transcripts
3. Generates AI-powered summaries using Claude API
4. Distributes summaries via email and Teams chat

The application is deployed internally on WSL2 and uses Microsoft Graph API with application permissions (service-to-service authentication, not user delegation).

## The Problem

Despite having all the correct Microsoft Graph API permissions granted with admin consent:
- ✅ OnlineMeetingTranscript.Read.All (Application)
- ✅ Chat.Read.All (Application)
- ✅ OnlineMeetings.Read.All (Application)
- ✅ All other required permissions

The application receives a 403 Forbidden error when attempting to download transcript content:

```
Error: "Application is not allowed to perform operations on the user,
neither is allowed access through RSC permission evaluation."
```

## Why This Happens

Microsoft Graph requires **two layers of authorization** for accessing meeting transcripts with application permissions:

1. **Graph API Permissions** (✅ Already configured)
2. **Application Access Policy** (❌ Missing - this is what we need)

According to Microsoft documentation, the Application Access Policy is a secondary security layer that explicitly authorizes specific applications to access online meeting data on behalf of users. Even with correct permissions, the policy is mandatory for app-only authentication scenarios.

**Official Microsoft Documentation:**
- https://learn.microsoft.com/en-us/graph/cloud-communication-online-meeting-application-access-policy
- https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/meeting-transcripts/overview-transcripts

## Security Considerations

### What the Policy Does:
- Authorizes a **specific application ID** to access meeting transcripts
- Works in conjunction with (not instead of) Graph API permissions
- Does NOT grant any new permissions - only enables the app to use permissions already granted
- Can be scoped to specific users, security groups, or the entire organization

### Security Controls:
1. **Application Identity**: Policy is tied to app ID `a55e7e21-9850-4453-9a09-7ce7b6347b49`
2. **Existing Permissions**: App can only do what Graph API permissions allow (no additional capabilities)
3. **Audit Logs**: All Graph API activity is logged in Microsoft 365 audit logs
4. **Revocable**: Policy can be removed at any time via PowerShell
5. **Client Secret Protection**: Application credentials are stored locally in encrypted .env file, not exposed

### Risk Assessment:
- **Threat Model**: If application credentials are compromised, attacker could read meeting transcripts
- **Mitigation**: Credentials stored on internal server, access restricted, not committed to source control
- **Risk Level**: Equivalent to other internal service accounts with Graph API access

## Implementation Options

### Option 1: Conservative Approach (Recommended for Initial Testing)

Grant policy to specific users or a security group first:

```powershell
# Connect to Microsoft Teams
Connect-MicrosoftTeams

# Create the Application Access Policy
New-CsApplicationAccessPolicy `
  -Identity "Teams-Notetaker-Policy" `
  -AppIds "a55e7e21-9850-4453-9a09-7ce7b6347b49" `
  -Description "Allow Teams Notetaker app to access online meetings and transcripts"

# Grant to specific test users
Grant-CsApplicationAccessPolicy `
  -PolicyName "Teams-Notetaker-Policy" `
  -Identity "scott.schatz@townsquaremedia.com"

Grant-CsApplicationAccessPolicy `
  -PolicyName "Teams-Notetaker-Policy" `
  -Identity "edwin.wilson@townsquaremedia.com"

# Verify
Get-CsApplicationAccessPolicy -Identity "Teams-Notetaker-Policy"
```

### Option 2: Organization-Wide Deployment (For Production)

Grant policy to all users in the organization:

```powershell
# Connect to Microsoft Teams
Connect-MicrosoftTeams

# Create the Application Access Policy
New-CsApplicationAccessPolicy `
  -Identity "Teams-Notetaker-Policy" `
  -AppIds "a55e7e21-9850-4453-9a09-7ce7b6347b49" `
  -Description "Allow Teams Notetaker app to access online meetings and transcripts org-wide"

# Grant globally to entire organization
Grant-CsApplicationAccessPolicy `
  -PolicyName "Teams-Notetaker-Policy" `
  -Global

# Verify
Get-CsApplicationAccessPolicy -Identity "Teams-Notetaker-Policy"
```

### Option 3: Security Group Based (Best of Both Worlds)

Grant policy to a specific security group for easier management:

```powershell
# Connect to Microsoft Teams
Connect-MicrosoftTeams

# Create the Application Access Policy
New-CsApplicationAccessPolicy `
  -Identity "Teams-Notetaker-Policy" `
  -AppIds "a55e7e21-9850-4453-9a09-7ce7b6347b49" `
  -Description "Allow Teams Notetaker app to access online meetings and transcripts"

# Grant to security group
Grant-CsApplicationAccessPolicy `
  -PolicyName "Teams-Notetaker-Policy" `
  -Identity "transcript-enabled-users@townsquaremedia.com"

# Verify
Get-CsApplicationAccessPolicy -Identity "Teams-Notetaker-Policy"
```

## Prerequisites

- **Permissions Required**: Global Administrator or Teams Administrator role
- **PowerShell Module**: Microsoft Teams PowerShell module
- **Installation** (if not already installed):
  ```powershell
  Install-Module -Name MicrosoftTeams -Force -AllowClobber
  ```

## Post-Implementation

### Propagation Time
Changes can take **up to 30 minutes** to propagate through Microsoft's infrastructure. The application should be tested after this period.

### Verification Steps

1. **Verify policy exists:**
   ```powershell
   Get-CsApplicationAccessPolicy -Identity "Teams-Notetaker-Policy"
   ```

2. **Check user assignment:**
   ```powershell
   Get-CsOnlineUser -Identity "scott.schatz@townsquaremedia.com" |
     Select-Object UserPrincipalName, ApplicationAccessPolicy
   ```

3. **Test transcript access:**
   Development team will run tests to confirm 403 errors are resolved

### Expected Outcome
Once the policy is active and propagated:
- Application will successfully download meeting transcripts
- 403 "RSC permission evaluation" errors will be resolved
- Automated summarization pipeline will be fully operational

## Rollback Procedure

If the policy needs to be removed:

```powershell
# Remove from specific users
Grant-CsApplicationAccessPolicy `
  -PolicyName $null `
  -Identity "user@townsquaremedia.com"

# Remove global assignment
Grant-CsApplicationAccessPolicy `
  -PolicyName $null `
  -Global

# Delete the policy entirely
Remove-CsApplicationAccessPolicy -Identity "Teams-Notetaker-Policy"
```

## Questions or Concerns

If you have any questions about this setup or need additional information:

**Technical Contact**: Scott Schatz (scott.schatz@townsquaremedia.com)

**Application Details**:
- **App Registration ID**: a55e7e21-9850-4453-9a09-7ce7b6347b49
- **App Name**: SharePoint (existing registration, repurposed)
- **Deployment Location**: Internal WSL2 server
- **Source Code**: https://github.com/scottschatz/teams-notetaker

## References

- [Microsoft Graph Application Access Policy Documentation](https://learn.microsoft.com/en-us/graph/cloud-communication-online-meeting-application-access-policy)
- [Fetch Meeting Transcripts & Recordings - Microsoft Teams](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/meeting-transcripts/overview-transcripts)
- [New-CsApplicationAccessPolicy PowerShell Reference](https://learn.microsoft.com/en-us/powershell/module/teams/new-csapplicationaccesspolicy)
- [Grant-CsApplicationAccessPolicy PowerShell Reference](https://learn.microsoft.com/en-us/powershell/module/teams/grant-csapplicationaccesspolicy)

---

**Document Version**: 1.0
**Last Updated**: December 10, 2025
**Prepared By**: Development Team
