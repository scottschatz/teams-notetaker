"""
Microsoft Graph API - Teams Chat Posting

Posts meeting summaries to Teams chat threads.
Supports posting to meeting chats and channels.
"""

import logging
from typing import Dict, Any, Optional
import markdown2

from ..graph.client import GraphAPIClient
from ..core.exceptions import TeamsChatPostError


logger = logging.getLogger(__name__)


class TeamsChatPoster:
    """
    Posts messages to Teams chats via Microsoft Graph API.

    Graph API endpoints:
    - List chats: /me/chats or /users/{userId}/chats
    - Send message: /chats/{chatId}/messages
    - List channels: /teams/{teamId}/channels
    - Send to channel: /teams/{teamId}/channels/{channelId}/messages

    Usage:
        client = GraphAPIClient(config)
        poster = TeamsChatPoster(client)

        # Post summary to meeting chat
        poster.post_meeting_summary(
            chat_id="19:meeting_...",
            summary_markdown="## Summary\n\n...",
            meeting_metadata={...}
        )
    """

    def __init__(self, client: GraphAPIClient):
        """
        Initialize Teams chat poster.

        Args:
            client: GraphAPIClient instance
        """
        self.client = client

    def post_meeting_summary(
        self,
        chat_id: str,
        summary_markdown: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Optional[Dict[str, Any]] = None,
        include_header: bool = True
    ) -> str:
        """
        Post meeting summary to Teams chat.

        Args:
            chat_id: Chat thread ID (from onlineMeeting.chatInfo.threadId)
            summary_markdown: Summary in markdown format
            meeting_metadata: Meeting details
            include_header: Include meeting info header

        Returns:
            Message ID

        Raises:
            TeamsChatPostError: If posting fails
        """
        try:
            logger.info(f"Posting meeting summary to chat {chat_id}")

            # Build message content with enhanced data if available
            if enhanced_summary_data:
                full_message = self._build_enhanced_chat_message(
                    summary_markdown,
                    meeting_metadata,
                    enhanced_summary_data,
                    include_header
                )
            else:
                # Fallback to basic format
                if include_header:
                    header = self._build_chat_header(meeting_metadata)
                    full_message = f"{header}\n\n{summary_markdown}"
                else:
                    full_message = summary_markdown

            # Post message
            message_id = self.post_message(chat_id, full_message)

            logger.info(f"✓ Posted summary to chat (message_id: {message_id})")

            return message_id

        except Exception as e:
            logger.error(f"Failed to post meeting summary: {e}", exc_info=True)
            raise TeamsChatPostError(f"Chat post failed: {e}")

    def post_message(
        self,
        chat_id: str,
        content: str,
        content_type: str = "text"
    ) -> str:
        """
        Post message to Teams chat.

        Args:
            chat_id: Chat thread ID
            content: Message content (text or HTML)
            content_type: Content type ("text" or "html")

        Returns:
            Message ID

        Raises:
            TeamsChatPostError: If posting fails
        """
        try:
            # Build message payload
            message = {
                "body": {
                    "contentType": content_type,
                    "content": content
                }
            }

            # Send message
            endpoint = f"/chats/{chat_id}/messages"

            logger.debug(f"Posting message to chat {chat_id}")

            response = self.client.post(endpoint, json=message)

            message_id = response.get("id", "")

            logger.debug(f"Message posted: {message_id}")

            return message_id

        except Exception as e:
            logger.error(f"Failed to post message: {e}", exc_info=True)
            raise TeamsChatPostError(f"Failed to post message: {e}")

    def post_to_channel(
        self,
        team_id: str,
        channel_id: str,
        subject: str,
        content: str,
        content_type: str = "html"
    ) -> str:
        """
        Post message to Teams channel.

        Args:
            team_id: Team ID
            channel_id: Channel ID
            subject: Message subject/title
            content: Message content
            content_type: Content type ("text" or "html")

        Returns:
            Message ID

        Raises:
            TeamsChatPostError: If posting fails
        """
        try:
            logger.info(f"Posting message to channel {channel_id} in team {team_id}")

            # Build message payload
            message = {
                "subject": subject,
                "body": {
                    "contentType": content_type,
                    "content": content
                }
            }

            # Send message
            endpoint = f"/teams/{team_id}/channels/{channel_id}/messages"

            response = self.client.post(endpoint, json=message)

            message_id = response.get("id", "")

            logger.info(f"✓ Posted to channel (message_id: {message_id})")

            return message_id

        except Exception as e:
            logger.error(f"Failed to post to channel: {e}", exc_info=True)
            raise TeamsChatPostError(f"Channel post failed: {e}")

    def reply_to_message(
        self,
        chat_id: str,
        parent_message_id: str,
        content: str,
        content_type: str = "text"
    ) -> str:
        """
        Reply to an existing message in a chat.

        Args:
            chat_id: Chat thread ID
            parent_message_id: ID of message to reply to
            content: Reply content
            content_type: Content type

        Returns:
            Reply message ID

        Raises:
            TeamsChatPostError: If reply fails
        """
        try:
            logger.debug(f"Replying to message {parent_message_id} in chat {chat_id}")

            message = {
                "body": {
                    "contentType": content_type,
                    "content": content
                }
            }

            endpoint = f"/chats/{chat_id}/messages/{parent_message_id}/replies"

            response = self.client.post(endpoint, json=message)

            message_id = response.get("id", "")

            return message_id

        except Exception as e:
            logger.error(f"Failed to reply to message: {e}")
            raise TeamsChatPostError(f"Reply failed: {e}")

    def get_meeting_chat_id(self, meeting_id: str, user_id: str) -> Optional[str]:
        """
        Get chat thread ID for a meeting.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID (organizer or participant)

        Returns:
            Chat thread ID or None if not found

        Note: This requires the onlineMeeting object to have chatInfo populated.
        """
        try:
            logger.debug(f"Getting chat ID for meeting {meeting_id}")

            endpoint = f"/users/{user_id}/onlineMeetings/{meeting_id}"

            # Get meeting with chatInfo
            params = {"$select": "id,chatInfo"}

            meeting = self.client.get(endpoint, params=params)

            chat_info = meeting.get("chatInfo", {})
            chat_id = chat_info.get("threadId")

            if chat_id:
                logger.debug(f"Found chat ID: {chat_id}")
                return chat_id
            else:
                logger.warning(f"No chat ID found for meeting {meeting_id}")
                return None

        except Exception as e:
            logger.error(f"Failed to get meeting chat ID: {e}")
            return None

    def _build_chat_header(self, meeting_metadata: Dict[str, Any]) -> str:
        """
        Build formatted header for chat message.

        Args:
            meeting_metadata: Meeting details

        Returns:
            Formatted header string
        """
        subject = meeting_metadata.get("subject", "Meeting")
        organizer = meeting_metadata.get("organizer_name", "Unknown")
        start_time = meeting_metadata.get("start_time", "")
        duration = meeting_metadata.get("duration_minutes", 0)

        # Format start time
        if isinstance(start_time, str):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                start_time_formatted = dt.strftime("%B %d, %Y at %I:%M %p")
            except:
                start_time_formatted = start_time
        else:
            start_time_formatted = str(start_time)

        header = f"""**📝 Meeting Summary**

**Meeting:** {subject}
**Organizer:** {organizer}
**Date:** {start_time_formatted}
**Duration:** {duration} minutes

---
"""

        return header

    def _build_enhanced_chat_message(
        self,
        summary_markdown: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Dict[str, Any],
        include_header: bool = True
    ) -> str:
        """
        Build enhanced chat message with action items, decisions, and links.

        Args:
            summary_markdown: Summary text
            meeting_metadata: Meeting details
            enhanced_summary_data: Enhanced summary with structured data
            include_header: Include meeting header

        Returns:
            Formatted markdown message for Teams chat
        """
        # Extract enhanced data
        action_items = enhanced_summary_data.get("action_items", [])
        decisions = enhanced_summary_data.get("decisions", [])
        highlights = enhanced_summary_data.get("highlights", [])

        # Get SharePoint links
        transcript_url = meeting_metadata.get("transcript_sharepoint_url", "")
        recording_url = meeting_metadata.get("recording_sharepoint_url", "") or meeting_metadata.get("recording_url", "")

        # Build message
        message = ""

        if include_header:
            message += self._build_chat_header(meeting_metadata)

        # Links section
        message += "\n**🔗 Resources:**\n"
        if recording_url:
            message += f"- [🎥 Watch Recording]({recording_url})\n"
        if transcript_url:
            message += f"- [📄 View Transcript (Teams)]({transcript_url})\n"
        message += "\n"

        # Action items section
        if action_items:
            message += "## ✅ Action Items\n\n"
            for item in action_items[:10]:  # Limit to 10 for chat readability
                description = item.get("description", "")
                assignee = item.get("assignee", "")
                deadline = item.get("deadline", "")

                message += f"- [ ] **{description}**"
                if assignee and assignee != "Unassigned":
                    message += f" (@{assignee})"
                if deadline and deadline != "Not specified":
                    message += f" - Due: {deadline}"
                message += "\n"

            if len(action_items) > 10:
                message += f"\n_...and {len(action_items) - 10} more action items_\n"
            message += "\n"

        # Decisions section
        if decisions:
            message += "## 🎯 Key Decisions\n\n"
            for decision in decisions[:5]:  # Limit to 5 for chat readability
                decision_text = decision.get("decision", "")
                reasoning = decision.get("reasoning", "")

                message += f"- **{decision_text}**"
                if reasoning:
                    message += f"\n  - _Why:_ {reasoning}"
                message += "\n"

            if len(decisions) > 5:
                message += f"\n_...and {len(decisions) - 5} more decisions_\n"
            message += "\n"

        # Highlights section
        if highlights:
            message += "## ⭐ Key Moments\n\n"
            for highlight in highlights[:5]:  # Limit to 5
                title = highlight.get("title", "")
                timestamp = highlight.get("timestamp", "")

                # Create timestamped link if recording URL available
                if recording_url and timestamp:
                    try:
                        parts = timestamp.split(":")
                        seconds = int(parts[0]) * 60 + int(parts[1])
                        message += f"- [{title}]({recording_url}#t={seconds}) ({timestamp})\n"
                    except:
                        message += f"- {title} ({timestamp})\n"
                else:
                    message += f"- {title}"
                    if timestamp:
                        message += f" ({timestamp})"
                    message += "\n"

            if len(highlights) > 5:
                message += f"\n_...and {len(highlights) - 5} more highlights_\n"
            message += "\n"

        # Full summary
        message += "## 📝 Full Summary\n\n"
        message += summary_markdown
        message += "\n\n---\n\n"

        # Commands footer
        message += "**💬 Chat Commands:**\n"
        message += "- Reply `@meeting notetaker email me` to get a personalized summary\n"
        message += "- Reply `@meeting notetaker no emails` to opt out of future email summaries\n"
        message += "- Reply `@meeting notetaker summarize again [your instructions]` to regenerate with custom focus\n"

        return message

    def send_test_message(self, chat_id: str) -> bool:
        """
        Send a test message to verify chat posting.

        Args:
            chat_id: Chat thread ID

        Returns:
            True if posted successfully
        """
        try:
            logger.info(f"Sending test message to chat {chat_id}")

            message_id = self.post_message(
                chat_id=chat_id,
                content="✓ Test message from Teams Meeting Transcript Summarizer. Chat posting is working!",
                content_type="text"
            )

            logger.info(f"✓ Test message sent (message_id: {message_id})")
            return True

        except Exception as e:
            logger.error(f"✗ Test message failed: {e}")
            return False
