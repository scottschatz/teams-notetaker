"""
Inbox Reader

Reads emails from a shared mailbox using Microsoft Graph API.
Supports filtering by date and tracking processed messages.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ..graph.client import GraphAPIClient
from ..core.exceptions import GraphAPIError


logger = logging.getLogger(__name__)


class InboxReader:
    """
    Reads emails from a shared mailbox via Microsoft Graph API.

    The shared mailbox is typically the 'from' address used for sending
    meeting summaries (e.g., notetaker@company.com).
    """

    def __init__(self, graph_client: GraphAPIClient, mailbox_email: str):
        """
        Initialize inbox reader.

        Args:
            graph_client: Authenticated Graph API client
            mailbox_email: Email address of the shared mailbox to monitor
        """
        self.graph_client = graph_client
        self.mailbox_email = mailbox_email

    def get_recent_messages(
        self,
        since: datetime,
        max_messages: int = 50,
        folder: str = "inbox"
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages from the mailbox.

        Args:
            since: Only return messages received after this time (UTC)
            max_messages: Maximum number of messages to return
            folder: Mail folder to read from (default: inbox)

        Returns:
            List of message dictionaries with keys:
                - id: Graph API message ID
                - subject: Email subject
                - body_preview: First ~255 chars of body
                - body_content: Full body text
                - from_email: Sender email address
                - from_name: Sender display name
                - received_datetime: When message was received
                - internet_message_id: Unique message ID for deduplication
        """
        try:
            # Format datetime for Graph API filter
            since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Build endpoint
            endpoint = f"/users/{self.mailbox_email}/mailFolders/{folder}/messages"

            params = {
                "$filter": f"receivedDateTime ge {since_str}",
                "$select": "id,subject,bodyPreview,body,from,receivedDateTime,internetMessageId,conversationId",
                "$orderby": "receivedDateTime desc",
                "$top": max_messages
            }

            logger.debug(f"Fetching messages from {self.mailbox_email} since {since_str}")

            response = self.graph_client.get(endpoint, params=params)
            messages = response.get("value", [])

            logger.info(f"Found {len(messages)} messages in {self.mailbox_email} since {since_str}")

            # Transform to standard format
            result = []
            for msg in messages:
                from_data = msg.get("from", {}).get("emailAddress", {})
                body_data = msg.get("body", {})

                result.append({
                    "id": msg.get("id"),
                    "subject": msg.get("subject", ""),
                    "body_preview": msg.get("bodyPreview", ""),
                    "body_content": self._extract_text_from_body(body_data),
                    "from_email": from_data.get("address", "").lower(),
                    "from_name": from_data.get("name", ""),
                    "received_datetime": msg.get("receivedDateTime"),
                    "internet_message_id": msg.get("internetMessageId", ""),
                    "conversation_id": msg.get("conversationId", ""),
                })

            return result

        except GraphAPIError as e:
            logger.error(f"Failed to read inbox: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error reading inbox: {e}", exc_info=True)
            raise GraphAPIError(f"Failed to read inbox: {e}")

    def _extract_text_from_body(self, body_data: Dict[str, Any]) -> str:
        """
        Extract plain text from email body.

        Args:
            body_data: Body object from Graph API with contentType and content

        Returns:
            Plain text content
        """
        content = body_data.get("content", "")
        content_type = body_data.get("contentType", "text")

        if content_type.lower() == "html":
            # Strip HTML tags for plain text
            import re
            # Remove script and style elements
            content = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
            # Remove HTML tags
            content = re.sub(r'<[^>]+>', ' ', content)
            # Clean up whitespace
            content = re.sub(r'\s+', ' ', content).strip()

        return content

    def mark_as_read(self, message_id: str) -> bool:
        """
        Mark a message as read.

        Args:
            message_id: Graph API message ID

        Returns:
            True if successful
        """
        try:
            endpoint = f"/users/{self.mailbox_email}/messages/{message_id}"
            self.graph_client.patch(endpoint, json={"isRead": True})
            logger.debug(f"Marked message {message_id[:20]}... as read")
            return True
        except Exception as e:
            logger.warning(f"Failed to mark message as read: {e}")
            return False

    def permanent_delete(self, message_id: str) -> bool:
        """
        Permanently delete a message (bypasses Deleted Items folder).

        Uses the Graph API permanentDelete action to hard delete the message
        without moving it to trash first.

        Args:
            message_id: Graph API message ID

        Returns:
            True if successful
        """
        try:
            endpoint = f"/users/{self.mailbox_email}/messages/{message_id}/permanentDelete"
            self.graph_client.post(endpoint)
            logger.debug(f"Permanently deleted message {message_id[:20]}...")
            return True
        except Exception as e:
            logger.warning(f"Failed to permanently delete message: {e}")
            return False

    def send_reply(
        self,
        message_id: str,
        reply_body: str,
        reply_subject: Optional[str] = None
    ) -> bool:
        """
        Send a reply to a message.

        Args:
            message_id: Graph API message ID to reply to
            reply_body: HTML body content for reply
            reply_subject: Optional custom subject (default: RE: original subject)

        Returns:
            True if successful
        """
        try:
            endpoint = f"/users/{self.mailbox_email}/messages/{message_id}/reply"

            payload = {
                "message": {
                    "body": {
                        "contentType": "HTML",
                        "content": reply_body
                    }
                }
            }

            self.graph_client.post(endpoint, json=payload)
            logger.info(f"Sent reply to message {message_id[:20]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to send reply: {e}")
            return False

    def send_acknowledgment(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        body_html: str
    ) -> bool:
        """
        Send an acknowledgment email (not a reply).

        Args:
            to_email: Recipient email address
            to_name: Recipient display name
            subject: Email subject
            body_html: HTML body content

        Returns:
            True if successful
        """
        try:
            endpoint = f"/users/{self.mailbox_email}/sendMail"

            payload = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML",
                        "content": body_html
                    },
                    "toRecipients": [
                        {
                            "emailAddress": {
                                "address": to_email,
                                "name": to_name
                            }
                        }
                    ]
                }
            }

            self.graph_client.post(endpoint, json=payload)
            logger.info(f"Sent acknowledgment to {to_email}: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send acknowledgment to {to_email}: {e}")
            return False
