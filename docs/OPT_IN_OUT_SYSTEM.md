# Meeting Notetaker - Opt-In/Opt-Out System

**Status**: ✅ **Fully Implemented** (but not yet integrated into distribution flow)
**Last Updated**: December 16, 2025

---

## Executive Summary

The Teams Meeting Notetaker has a complete opt-in/opt-out system for email summaries, controlled via chat commands in Teams meeting chats. Users can manage their preferences by mentioning the bot in the meeting chat.

**Current Default Behavior**: **Opt-In** (all participants receive emails automatically unless they opt out)

---

## Available Commands

### For All Users

#### 📧 Get Personalized Email
```
@meeting notetaker email me
```
- Sends personalized email to the requesting user
- Includes times they were mentioned
- Highlights their assigned action items
- Works even if user has opted out of automatic emails
- **Alternative**: React with 📧 emoji to any summary message

#### 🚫 Opt Out of Automatic Emails
```
@meeting notetaker no emails
```
- Opts user out of all automatic email summaries
- User can still request emails on-demand using "email me" command
- Summaries will still be posted in Teams chat (visible to everyone)
- Preference persists across all meetings

**Supported variations:**
- `@meeting notetaker opt out`
- `@meeting notetaker unsubscribe`
- `@meeting notetaker stop emails`
- `@meeting notetaker don't email me`

#### ❓ Get Help
```
@meeting notetaker help
```
Shows available commands and usage instructions.

---

### For Meeting Organizers Only

#### 📨 Send Summary to All Participants
```
@meeting notetaker email all
```
- Sends standard summary email to all participants
- **Organizer-only command** (others will get error)
- Useful for manually triggering distribution

**Supported variations:**
- `@meeting notetaker email everyone`
- `@meeting notetaker send to all`

#### 🔄 Re-Summarize with Custom Instructions
```
@meeting notetaker summarize again focus on engineering decisions
```
- Regenerates summary with custom focus
- Creates new version (keeps old version)
- Useful for tailoring summary to specific needs

**Example custom instructions:**
- "focus on action items for the engineering team"
- "emphasize budget and cost discussions"
- "highlight risks and blockers only"

---

## How It Works

### 1. Chat Monitoring
The system monitors Teams meeting chats for bot mentions:
- **Polling interval**: Every 5-10 minutes (configurable)
- **Detection**: Looks for `@meeting notetaker` or `@meetingnotetaker`
- **Deduplication**: Tracks processed messages to avoid duplicates

### 2. Command Processing
When a command is detected:
1. **Parse**: Extract command type and parameters
2. **Validate**: Check command is valid and user has permission
3. **Queue**: Create job for asynchronous processing
4. **Confirm**: Post confirmation message to chat
5. **Execute**: Process command (send email, update preferences, etc.)

### 3. Preference Storage
User preferences are stored in database:
- **Table**: `user_preferences`
- **Default**: `receive_emails = TRUE` (opt-in by default)
- **Scope**: Global (applies to all meetings)
- **Persistence**: Permanent until user changes

### 4. Distribution Flow
When distributing meeting summaries:

**Current Behavior** (as implemented):
1. Get all meeting participants
2. Send email to ALL participants
3. Post summary to Teams chat

**Intended Behavior** (not yet integrated):
1. Get all meeting participants
2. **Filter by preferences** (remove opted-out users)
3. Send email to opted-in participants only
4. Post summary to Teams chat

---

## Default Behavior Options

You have three options for default email behavior:

### Option 1: Opt-In by Default (CURRENT)
**Default**: Everyone receives emails automatically

**Pros:**
- ✅ Maximum reach - no one misses summaries
- ✅ Users expect meeting follow-ups via email
- ✅ Low friction for adoption
- ✅ Matches Microsoft Teams default behavior

**Cons:**
- ⚠️ Users who don't want emails must manually opt out
- ⚠️ May be perceived as spam initially

**Best for:**
- Organizations with strong meeting culture
- Teams that want comprehensive distribution
- Users who prefer email for meeting notes

---

### Option 2: Opt-Out by Default
**Default**: No one receives emails unless they request

**Pros:**
- ✅ Respects user privacy and inbox preferences
- ✅ No risk of being perceived as spam
- ✅ Compliant with stricter email policies

**Cons:**
- ⚠️ Low adoption (users must discover feature)
- ⚠️ Defeats purpose of automatic summarization
- ⚠️ Requires training users to use commands

**Best for:**
- Organizations with strict email policies
- Pilot/test deployments
- Privacy-focused environments

---

### Option 3: Organizer Controls Default (RECOMMENDED)
**Default**: Organizer decides per-meeting or per-series

**Pros:**
- ✅ Balances reach with user control
- ✅ Organizer knows if meeting needs follow-up
- ✅ Flexible for different meeting types

**Cons:**
- ⚠️ Requires organizer awareness of feature
- ⚠️ More complex implementation

**Best for:**
- Mature deployments
- Organizations with varied meeting types
- When you want maximum flexibility

**Implementation:**
- Add `auto_send_emails` flag to Meeting model
- Default to `TRUE` or `FALSE` based on org preference
- Allow organizer to override via chat command:
  ```
  @meeting notetaker enable auto emails
  @meeting notetaker disable auto emails
  ```

---

## Implementation Status

### ✅ Implemented Features

| Feature | Status | File |
|---------|--------|------|
| Command parser | ✅ Complete | `src/chat/command_parser.py` |
| Chat monitor | ✅ Complete | `src/chat/chat_monitor.py` |
| Preference manager | ✅ Complete | `src/preferences/user_preferences.py` |
| Chat command processor | ✅ Complete | `src/jobs/processors/chat_command.py` |
| Database schema | ✅ Complete | `src/core/database.py` (UserPreference table) |
| Default behavior | ✅ Opt-in | `user_preferences.py:72` |

### ⚠️ Partially Implemented

| Feature | Status | What's Missing |
|---------|--------|----------------|
| Distribution filtering | ⚠️ Not integrated | Distribution processor doesn't use PreferenceManager |
| Chat monitoring loop | ⚠️ Not running | Not integrated into poller or worker |

### ❌ Known Issues

| Issue | Impact | Workaround |
|-------|--------|------------|
| Chat posting permissions | High | Emails work, but chat confirmations fail silently |
| Distribution ignores preferences | High | All users get emails regardless of opt-out |
| No chat monitoring active | High | Commands not detected automatically |

---

## Integration Requirements

### To Enable Full Functionality:

#### 1. Integrate Preferences into Distribution
**File**: `src/jobs/processors/distribution.py`

**Add after line 110** (after getting participant_emails):
```python
from ...preferences import PreferenceManager

# Filter participants by email preferences
pref_manager = PreferenceManager(self.db)
participant_emails = pref_manager.get_opted_in_emails(participant_emails)

self._log_progress(
    job,
    f"After filtering preferences: {len(participant_emails)} opted-in recipient(s)"
)

if not participant_emails:
    self._log_progress(job, "No opted-in participants, skipping email", "warning")
    # Still post to chat, just don't send emails
```

#### 2. Add Chat Monitoring to Poller
**File**: `src/discovery/poller.py`

Add chat monitoring loop to check for commands:
```python
from ..chat.chat_monitor import ChatMonitor
from ..chat.command_parser import ChatCommandParser

# Initialize in __init__:
self.chat_parser = ChatCommandParser()
self.chat_monitor = ChatMonitor(self.graph_client, self.db, self.chat_parser)

# Add new method:
def _check_for_chat_commands(self):
    """Check recent meetings for chat commands."""
    with self.db.get_session() as session:
        # Get meetings with chats from last 48 hours
        recent_meetings = session.query(Meeting).filter(
            Meeting.chat_id.isnot(None),
            Meeting.start_time > datetime.now() - timedelta(hours=48)
        ).all()

        for meeting in recent_meetings:
            commands = self.chat_monitor.check_for_commands(
                chat_id=meeting.chat_id,
                since=datetime.now() - timedelta(hours=24)
            )

            for command in commands:
                # Create chat_command job
                self.queue.create_job(
                    job_type="process_chat_command",
                    input_data={
                        "command_type": command.command_type.value,
                        "meeting_id": meeting.id,
                        "message_id": command.message_id,
                        "chat_id": command.chat_id,
                        "user_email": command.user_email,
                        "user_name": command.user_name,
                        "parameters": command.parameters,
                        "raw_message": command.raw_message
                    },
                    priority=7
                )

# Call from run_loop():
self._check_for_chat_commands()
```

#### 3. Grant Graph API Permissions
**Required permissions**:
- `Chat.ReadWrite` or `ChatMessage.Send` - To post confirmation messages
- `ChatMessage.Read.All` - To monitor chat for commands

**How to add**:
1. Go to Azure Portal → App Registrations
2. Select your application
3. API Permissions → Add a permission → Microsoft Graph
4. Select "Application permissions"
5. Add: `Chat.ReadWrite`, `ChatMessage.Read.All`
6. Click "Grant admin consent"

---

## Testing the System

### Test Scenario 1: Opt Out
1. Join a meeting that's being recorded
2. In the meeting chat, type: `@meeting notetaker no emails`
3. Wait for confirmation message
4. Verify: Next meeting summary should NOT be emailed to you
5. Teams chat should still show the summary

### Test Scenario 2: On-Demand Email
1. After opting out, type: `@meeting notetaker email me`
2. Wait for confirmation message
3. Verify: You receive a personalized email despite being opted out

### Test Scenario 3: Organizer Commands
1. As meeting organizer, type: `@meeting notetaker email all`
2. Wait for confirmation message
3. Verify: All participants receive email

### Test Scenario 4: Re-Summarize
1. As organizer, type: `@meeting notetaker summarize again focus on budget`
2. Wait for confirmation message
3. Verify: New summary generated with custom focus
4. Check database: `summaries.version` should increment

---

## Database Schema

### UserPreference Table
```sql
CREATE TABLE user_preferences (
    id SERIAL PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL UNIQUE,
    receive_emails BOOLEAN DEFAULT TRUE,
    email_preference VARCHAR(50) DEFAULT 'all',  -- 'all', 'mentions_only', 'disabled'
    updated_by VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_prefs_email ON user_preferences(user_email);
```

### ProcessedChatMessage Table
```sql
CREATE TABLE processed_chat_messages (
    id SERIAL PRIMARY KEY,
    message_id VARCHAR(255) NOT NULL UNIQUE,
    chat_id VARCHAR(255) NOT NULL,
    command_type VARCHAR(50),
    result TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_processed_chat_messages ON processed_chat_messages(message_id);
CREATE INDEX idx_processed_chat_chat_id ON processed_chat_messages(chat_id);
```

---

## Configuration

### Config Options (config.yaml)
```yaml
chat_monitoring:
  enabled: true
  poll_interval_seconds: 300  # Check every 5 minutes
  lookback_hours: 24  # Check messages from last 24 hours
  max_messages_per_poll: 50

preferences:
  default_behavior: "opt_in"  # Options: "opt_in", "opt_out", "organizer_controlled"
  allow_user_opt_out: true
  allow_organizer_override: true
```

### Environment Variables (.env)
```bash
# Email Distribution
EMAIL_DEFAULT_OPT_IN=true  # true = opt-in by default, false = opt-out by default

# Chat Monitoring
CHAT_MONITORING_ENABLED=true
CHAT_POLL_INTERVAL=300
```

---

## Monitoring & Analytics

### Preference Statistics
```python
from src.preferences import PreferenceManager

pref_manager = PreferenceManager(db)
stats = pref_manager.get_preference_stats()

print(stats)
# {
#     "total_users": 150,
#     "opted_in": 142,
#     "opted_out": 8,
#     "opt_out_rate": 5.3
# }
```

### Chat Monitoring Statistics
```python
from src.chat.chat_monitor import ChatMonitor

monitor = ChatMonitor(graph_client, db, parser)
stats = monitor.get_monitoring_stats()

print(stats)
# {
#     "total_processed": 342,
#     "recent_activity": 12,  # Last hour
#     "commands_by_type": {
#         "email_me:success": 87,
#         "no_emails:success": 23,
#         "email_all:success": 45,
#         "invalid_command": 5
#     }
# }
```

---

## Recommendations

### For Your Organization:

**Recommended Default**: **Opt-In** (current implementation)

**Rationale:**
1. Meeting summaries are valuable follow-up communication
2. Users expect action items and decisions via email
3. Low friction for adoption - users get value immediately
4. Matches behavior of Microsoft Teams/Copilot
5. Opt-out is easy for those who prefer not to receive

**Suggested Rollout:**
1. **Pilot Phase** (Weeks 1-2):
   - Opt-in default
   - Announce feature in company-wide email
   - Include instructions for opting out
   - Monitor opt-out rate

2. **Feedback Phase** (Weeks 3-4):
   - Survey users about email frequency
   - Identify pain points
   - Adjust if opt-out rate exceeds 20%

3. **Scale Phase** (Weeks 5+):
   - Expand to all users
   - Keep opt-in default if opt-out rate < 20%
   - Consider organizer controls if needed

---

## FAQ

### Q: What happens if I opt out?
**A:** You won't receive automatic emails, but:
- Summaries still appear in Teams chat
- You can request personalized emails on-demand with "@meeting notetaker email me"
- You're still mentioned in summaries sent to others

### Q: Can I opt back in after opting out?
**A:** Not currently via chat command. Contact your admin or have them run:
```python
pref_manager.set_user_preference("user@example.com", receive_emails=True)
```
*Future enhancement: Add `@meeting notetaker enable emails` command*

### Q: Does opt-out apply to all meetings?
**A:** Yes, currently it's global across all meetings. Meeting-specific preferences are a future enhancement.

### Q: Can organizers override my preference?
**A:** Not currently. The `email all` command respects individual opt-out preferences (once integrated).

### Q: What about people not in the meeting?
**A:** Only meeting participants receive emails. External mentions don't trigger emails.

### Q: Do I get emails for meetings I didn't attend?
**A:** Currently yes (if you were invited). Future enhancement: filter by attendance.

---

## Future Enhancements

### Short-term (1-3 months):
- [ ] Integrate preferences into distribution flow
- [ ] Add chat monitoring to poller loop
- [ ] Grant required Graph API permissions
- [ ] Add "opt back in" command
- [ ] Add analytics dashboard for preference trends

### Medium-term (3-6 months):
- [ ] Meeting-specific preferences ("no emails for this recurring meeting")
- [ ] Organizer controls ("disable auto-emails for this meeting")
- [ ] Personalization options ("only email me if I'm mentioned or have action items")
- [ ] Digest mode ("send weekly digest instead of per-meeting")

### Long-term (6-12 months):
- [ ] AI-driven preference suggestions ("You haven't opened meeting emails in 30 days - would you like to opt out?")
- [ ] Slack integration (opt in via Slack instead of Teams)
- [ ] Calendar integration (set preferences by calendar)

---

## Support & Troubleshooting

### Issue: Commands not working
**Symptoms**: Bot doesn't respond to `@meeting notetaker` commands

**Causes**:
1. Chat monitoring not enabled in poller
2. Missing Graph API permissions
3. Bot mention not detected (wrong format)

**Solutions**:
1. Check `config.yaml`: `chat_monitoring.enabled = true`
2. Verify Graph API permissions in Azure Portal
3. Use exact format: `@meeting notetaker` (space required)

---

### Issue: Still getting emails after opt-out
**Symptoms**: User opts out but continues receiving emails

**Cause**: Distribution processor not using PreferenceManager

**Solution**: Apply integration fix in section "Integration Requirements #1"

---

### Issue: Chat posting fails silently
**Symptoms**: Commands work but no confirmation in chat

**Cause**: Missing `Chat.ReadWrite` permission

**Solution**: Grant permission in Azure Portal (see "Integration Requirements #3")

---

## Contact

**Questions about this system?**
- Check logs: `journalctl -u teams-notetaker-web -f`
- Check database: `SELECT * FROM user_preferences WHERE user_email = 'user@example.com';`
- Contact admin: [Your admin contact info]

**Feature requests?**
- File issue in GitHub repo
- Email: [Your email]

---

**Last Updated**: December 16, 2025
**Version**: 1.0
**Status**: Ready for integration
