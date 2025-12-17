# Teams Bot Registration - Next Steps Handoff

## Current Status (December 15, 2025)

### ✅ What's Working:
- **Transcript fetching** - Organization-wide access via Application Access Policy
- **AI summarization** - Claude generates summaries (3,617 chars for 166-min meeting)
- **Email distribution** - Sends to all participants with:
  - Speaker breakdown table
  - Executive summary
  - Next steps
  - SharePoint links to transcript/recording
  - Actual vs scheduled duration

### ❌ What's NOT Working:
- **Teams chat posting** - Graph API blocks with 401 error
  - Error: "Message POST is allowed in application-only context only for import purposes"
  - **Root cause**: Cannot post to chats using application permissions
  - **Impact**: Can't post summaries to meeting chats, can't receive commands

### 📋 Known Limitations:
- **Enhanced extraction failed** - JSON parsing errors (0 action items, 0 decisions, 0 topics)
  - Root cause: Enhanced summarizer's JSON parsing needs debugging
  - Impact: Missing structured data sections in emails

---

## Why Register a Teams Bot?

### Problem:
The current implementation uses **Graph API with application permissions** to try posting to Teams chats. Microsoft **explicitly blocks** this:

```
POST /chats/{chatId}/messages
401 Unauthorized: Message POST is allowed in application-only
context only for import purposes.
```

### Solution:
Register a **Teams Bot** using Bot Framework SDK, which:
- ✅ Can send proactive messages to chats
- ✅ Can receive messages/commands from users
- ✅ Can be installed in Teams without publishing to AppSource
- ✅ Uses the official Microsoft-supported approach

---

## What a Teams Bot Enables

### 1. Post Summaries to Meeting Chats ✅
**Current:** Summary only sent via email
**With Bot:** Post summary directly to meeting chat where it happened

**Benefits:**
- All participants see it in context
- No email to find/manage
- Accessible in Teams where they already are

### 2. Interactive Commands (Future) ⏳
**Current:** No interaction capability
**With Bot:** Users can:
- `@meeting notetaker email me` - Get personalized summary
- `@meeting notetaker summarize again focus on X` - Custom re-summarization
- `@meeting notetaker show action items` - Quick lookup

### 3. React to Messages (Future) ⏳
**Potential:** Users react with 📧 emoji → bot sends email copy

### 4. Real-time Notifications (Future) ⏳
**Potential:** Bot joins meeting, provides live captions, action item extraction

---

## Implementation Steps

### Phase 1: Register Bot (30 minutes)

#### 1.1. Create Bot in Azure Portal
```bash
# Login to Azure Portal
https://portal.azure.com

# Navigate: Create Resource → Bot Channels Registration
Name: teams-notetaker-bot
Resource Group: teams-notetaker-rg
Location: East US
Pricing: F0 (Free)
Microsoft App ID: Create new
```

**Result:** Get Bot App ID and Secret

#### 1.2. Add Teams Channel
```
Bot Settings → Channels → Add Microsoft Teams
Enable: Calling, Messaging
```

#### 1.3. Create App Manifest
```json
{
  "manifestVersion": "1.17",
  "version": "1.0.0",
  "id": "<BOT_APP_ID>",
  "packageName": "com.townsquaremedia.teams-notetaker",
  "developer": {
    "name": "Townsquare Media IT",
    "websiteUrl": "https://townsquaremedia.com",
    "privacyUrl": "https://townsquaremedia.com/privacy",
    "termsOfUseUrl": "https://townsquaremedia.com/terms"
  },
  "name": {
    "short": "Meeting Notetaker",
    "full": "Meeting Notetaker - AI Summary Bot"
  },
  "description": {
    "short": "Automatically summarize meeting transcripts",
    "full": "Posts AI-generated meeting summaries to Teams chats after meetings with transcripts"
  },
  "icons": {
    "color": "color.png",
    "outline": "outline.png"
  },
  "accentColor": "#60A18E",
  "bots": [
    {
      "botId": "<BOT_APP_ID>",
      "scopes": ["team", "personal", "groupchat"],
      "supportsFiles": false,
      "isNotificationOnly": false,
      "commandLists": [
        {
          "scopes": ["team", "personal", "groupchat"],
          "commands": [
            {
              "title": "email me",
              "description": "Send personalized summary to your email"
            },
            {
              "title": "help",
              "description": "Show available commands"
            }
          ]
        }
      ]
    }
  ],
  "permissions": ["identity", "messageTeamMembers"],
  "validDomains": []
}
```

#### 1.4. Upload to Teams
```bash
# Package manifest
cd teams-app
zip -r teams-notetaker.zip manifest.json color.png outline.png

# Upload:
Teams → Apps → Upload a custom app → Upload for Townsquare Media
```

---

### Phase 2: Implement Bot Code (2-3 hours)

#### 2.1. Install Dependencies
```bash
pip install botbuilder-core botbuilder-schema botframework-connector
```

#### 2.2. Create Bot Service
**New file:** `src/bots/teams_bot.py`

```python
"""
Teams Bot for posting meeting summaries.
Uses Bot Framework SDK for proactive messaging.
"""

from botbuilder.core import BotFrameworkAdapter, TurnContext
from botbuilder.schema import Activity, ConversationReference
from botframework.connector import ConnectorClient
from botframework.connector.auth import MicrosoftAppCredentials

class MeetingNotetakerBot:
    """Bot for posting meeting summaries to Teams chats."""

    def __init__(self, app_id: str, app_password: str):
        self.app_id = app_id
        self.app_password = app_password
        self.credentials = MicrosoftAppCredentials(app_id, app_password)
        self.adapter = BotFrameworkAdapter(self.credentials)

    async def post_summary_to_chat(
        self,
        chat_id: str,
        summary_markdown: str,
        meeting_metadata: dict
    ) -> str:
        """
        Post meeting summary to Teams chat.

        Args:
            chat_id: Teams chat ID (from meeting)
            summary_markdown: Formatted summary
            meeting_metadata: Meeting details

        Returns:
            Message ID of posted message
        """
        # Build conversation reference for chat
        conversation_ref = ConversationReference(
            conversation={"id": chat_id},
            service_url="https://smba.trafficmanager.net/amer/"
        )

        # Create adaptive card or markdown message
        message = Activity(
            type="message",
            text=summary_markdown,
            conversation=conversation_ref.conversation
        )

        # Send proactive message
        async def send_message(turn_context: TurnContext):
            await turn_context.send_activity(message)

        await self.adapter.continue_conversation(
            conversation_ref,
            send_message,
            self.app_id
        )

        return message.id
```

#### 2.3. Update Distribution Processor
**File:** `src/jobs/processors/distribution.py`

```python
# Replace chat poster with bot
from src.bots.teams_bot import MeetingNotetakerBot

# In __init__:
self.bot = MeetingNotetakerBot(
    app_id=config.bot.app_id,
    app_password=config.bot.app_password
)

# In process():
if self.config.app.teams_chat_enabled and meeting.chat_id:
    chat_message_id = await self.bot.post_summary_to_chat(
        chat_id=meeting.chat_id,
        summary_markdown=summary.summary_text,
        meeting_metadata=meeting_metadata
    )
```

#### 2.4. Add Bot Config
**File:** `.env`
```bash
# Teams Bot (Phase 2)
BOT_APP_ID=<your-bot-app-id>
BOT_APP_PASSWORD=<your-bot-secret>
```

---

### Phase 3: Test Bot (1 hour)

#### 3.1. Local Testing
```bash
# Start bot endpoint (if needed)
python -m src.bots.bot_server  # Optional: local webhook

# Run a test meeting
# Post a message to verify bot works
```

#### 3.2. Production Deploy
```bash
# Restart services
systemctl --user restart teams-notetaker-poller

# Trigger a test job
python -m src.main test-bot --meeting-id 172
```

#### 3.3. Verify in Teams
1. Check meeting chat for posted summary
2. Verify formatting (markdown/adaptive card)
3. Test command responses (if implemented)

---

## Architecture Changes

### Before (Current):
```
┌─────────────────┐
│ Poller/Worker   │
└────────┬────────┘
         │
         ├─→ Graph API (fetch transcripts) ✅
         ├─→ Claude API (generate summary) ✅
         ├─→ Graph API (send email) ✅
         └─→ Graph API (post to chat) ❌ 401 Error
```

### After (With Bot):
```
┌─────────────────┐
│ Poller/Worker   │
└────────┬────────┘
         │
         ├─→ Graph API (fetch transcripts) ✅
         ├─→ Claude API (generate summary) ✅
         ├─→ Graph API (send email) ✅
         └─→ Bot Framework (post to chat) ✅ WORKS!
```

---

## Estimated Effort

| Task | Time | Who |
|------|------|-----|
| Azure bot registration | 30 min | IT Admin |
| Create app manifest | 30 min | Developer |
| Upload to Teams | 15 min | IT Admin |
| Implement bot code | 2-3 hrs | Developer |
| Test & deploy | 1 hr | Developer |
| **Total** | **4-5 hrs** | |

---

## Risks & Mitigations

### Risk 1: Bot Permissions
**Risk:** Bot might need admin approval to install
**Mitigation:** IT admin can sideload for org

### Risk 2: Chat ID Changes
**Risk:** Chat IDs might change after bot joins
**Mitigation:** Store conversation references

### Risk 3: Proactive Messaging Limits
**Risk:** Bot might have rate limits
**Mitigation:** Queue messages, handle throttling

---

## Alternative: Don't Implement Bot

### If you decide NOT to implement the bot:

**Keep:**
- ✅ Email summaries (working great!)
- ✅ Transcript fetching (organization-wide)
- ✅ AI summarization (3,617 chars)
- ✅ Speaker breakdown (excellent UX)

**Remove:**
- ❌ Chat posting feature (doesn't work anyway)
- ❌ Chat command system (can't receive commands)
- ❌ "Reply in chat" instructions in email

**Email is the primary distribution channel** - and it works well!

---

## Recommendation

### Option A: Implement Bot (4-5 hours)
**Pros:**
- Summaries in chat where meeting happened
- Users already in Teams, no email needed
- Future: Interactive commands

**Cons:**
- 4-5 hours development time
- Additional infrastructure (bot registration)
- More complexity

### Option B: Email-Only (0 hours)
**Pros:**
- Already working perfectly
- Simpler architecture
- Email is familiar to users

**Cons:**
- No in-chat summaries
- No interactive commands

### My Recommendation: **Option B (Email-Only) for now**

**Reasoning:**
1. Email summaries are working great (you loved the one you got!)
2. Users get comprehensive summary with speaker breakdown
3. SharePoint links work for transcript/recording access
4. 4-5 hours development can be used elsewhere (fix JSON parsing!)
5. Can always add bot later if users request it

**Priority:** Fix the enhanced extraction (action items, decisions, topics) before adding chat posting.

---

## Next Steps (Your Choice)

### If You Want Bot:
1. Provide bot registration details to developer
2. Developer implements bot code (4-5 hrs)
3. Test in Teams
4. Deploy

### If Email-Only:
1. Fix enhanced extraction JSON parsing (higher ROI)
2. Remove chat-related code to simplify
3. Focus on email quality improvements

---

## Questions?

Contact: Claude AI Assistant (this session)
Date: December 15, 2025
Status: ✅ Ready for decision
