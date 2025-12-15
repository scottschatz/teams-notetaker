"""
Email Sender

Sends meeting summaries and notifications via Microsoft Graph API.
Uses delegated/application permissions to send email on behalf of users.
"""

import logging
from typing import List, Optional, Dict, Any
import markdown2
import re

from ..core.exceptions import EmailSendError
from ..graph.client import GraphAPIClient


logger = logging.getLogger(__name__)


class EmailSender:
    """
    Sends emails via Microsoft Graph API.

    Features:
    - Meeting summary emails with rich HTML formatting
    - Markdown to HTML conversion
    - CC/BCC support
    - Read receipts
    - Importance levels

    Usage:
        client = GraphAPIClient(tenant_id, client_id, client_secret)
        sender = EmailSender(client)

        sender.send_meeting_summary(
            from_email="noreply@company.com",
            to_emails=["user@company.com"],
            subject="Meeting Summary",
            summary_markdown="# Summary...",
            meeting_metadata={...}
        )
    """

    def __init__(self, client: GraphAPIClient):
        """
        Initialize email sender.

        Args:
            client: GraphAPIClient instance
        """
        self.client = client

    def send_meeting_summary(
        self,
        from_email: str,
        to_emails: List[str],
        subject: str,
        summary_markdown: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Optional[Dict[str, Any]] = None,
        transcript_content: Optional[str] = None,
        participants: Optional[List[Dict[str, str]]] = None,
        transcript_stats: Optional[Dict[str, Any]] = None,
        cc_emails: Optional[List[str]] = None,
        include_footer: bool = True
    ) -> str:
        """
        Send meeting summary email with enhancements.

        Args:
            from_email: Sender email (must have sendMail permission)
            to_emails: List of recipient emails
            subject: Email subject
            summary_markdown: Summary in markdown format
            meeting_metadata: Meeting details (organizer, date, join_url, recording_url, etc.)
            transcript_content: Optional transcript text for attachment
            participants: Optional list of participants
            transcript_stats: Optional stats (word_count, speaker_count)
            cc_emails: Optional CC recipients
            include_footer: Add standard footer with branding

        Returns:
            Message ID if sent successfully

        Raises:
            EmailSendError: If sending fails
        """
        try:
            logger.info(f"Sending meeting summary to {len(to_emails)} recipient(s)")

            # Convert markdown to HTML with better list support
            summary_html = markdown2.markdown(
                summary_markdown,
                extras=[
                    "tables",
                    "fenced-code-blocks",
                    "code-friendly",
                    "break-on-newline",
                    "cuddled-lists",
                    "header-ids"
                ]
            )

            # Extract action items for callout box
            action_items_html = self._extract_action_items(summary_html)

            # Build email body with all enhancements
            body_html = self._build_enhanced_email_body(
                summary_html,
                meeting_metadata,
                enhanced_summary_data,
                action_items_html,
                participants,
                transcript_stats,
                include_footer
            )

            # Prepare attachments
            attachments = []
            if transcript_content:
                # Convert transcript to clean TXT format
                transcript_txt = self._format_transcript_for_attachment(transcript_content)
                attachments.append({
                    "name": "meeting_transcript.txt",
                    "contentType": "text/plain",
                    "contentBytes": self._encode_base64(transcript_txt)
                })

            # Send email
            message_id = self.send_email(
                from_email=from_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                subject=subject,
                body_html=body_html,
                attachments=attachments,
                importance="normal"
            )

            logger.info(f"✓ Email sent successfully (message_id: {message_id})")

            return message_id

        except Exception as e:
            logger.error(f"Failed to send meeting summary email: {e}", exc_info=True)
            raise EmailSendError(f"Email send failed: {e}")

    def send_email(
        self,
        from_email: str,
        to_emails: List[str],
        subject: str,
        body_html: str,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, str]]] = None,
        importance: str = "normal",
        request_read_receipt: bool = False
    ) -> str:
        """
        Send email via Graph API.

        Args:
            from_email: Sender email address
            to_emails: List of recipient emails
            subject: Email subject
            body_html: Email body (HTML format)
            cc_emails: Optional CC recipients
            bcc_emails: Optional BCC recipients
            attachments: Optional list of attachments
            importance: Email importance (low, normal, high)
            request_read_receipt: Request read receipt

        Returns:
            Message ID

        Raises:
            EmailSendError: If sending fails
        """
        try:
            # Build recipient lists
            to_recipients = [{"emailAddress": {"address": email}} for email in to_emails]
            cc_recipients = [{"emailAddress": {"address": email}} for email in (cc_emails or [])]
            bcc_recipients = [{"emailAddress": {"address": email}} for email in (bcc_emails or [])]

            # Build message
            message = {
                "subject": subject,
                "importance": importance,
                "body": {
                    "contentType": "HTML",
                    "content": body_html
                },
                "toRecipients": to_recipients
            }

            if cc_recipients:
                message["ccRecipients"] = cc_recipients

            if bcc_recipients:
                message["bccRecipients"] = bcc_recipients

            if request_read_receipt:
                message["isReadReceiptRequested"] = True

            if attachments:
                message["attachments"] = attachments

            # Send via Graph API
            # Use /users/{id}/sendMail endpoint
            endpoint = f"/users/{from_email}/sendMail"

            payload = {
                "message": message,
                "saveToSentItems": True
            }

            self.client.post(endpoint, json=payload)

            logger.info(f"Email sent from {from_email} to {len(to_emails)} recipient(s)")

            # Graph API doesn't return message ID for sendMail, generate tracking ID
            import uuid
            return f"sent-{uuid.uuid4()}"

        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            raise EmailSendError(f"Graph API sendMail failed: {e}")

    def _extract_action_items(self, summary_html: str) -> Optional[str]:
        """
        Extract action items section from summary HTML for callout box.

        Args:
            summary_html: Full summary HTML

        Returns:
            Action items HTML or None if not found
        """
        # Find "Action Items" heading and extract until next heading
        pattern = r'<h2[^>]*>Action Items</h2>(.*?)(?=<h2|$)'
        match = re.search(pattern, summary_html, re.DOTALL | re.IGNORECASE)

        if match:
            return match.group(1).strip()

        return None

    def _format_transcript_for_attachment(self, transcript_content: str) -> str:
        """
        Format VTT transcript as clean text for attachment.

        Args:
            transcript_content: Raw transcript content (VTT format or parsed)

        Returns:
            Formatted plain text
        """
        # If it's already a list of dicts (parsed), format it
        if isinstance(transcript_content, list):
            lines = []
            for segment in transcript_content:
                speaker = segment.get("speaker", "Unknown")
                timestamp = segment.get("timestamp", "")
                text = segment.get("text", "")
                lines.append(f"[{timestamp}] {speaker}: {text}")
            return "\n".join(lines)

        # If it's a string, return as-is (assume it's already formatted)
        return transcript_content

    def _encode_base64(self, text: str) -> str:
        """
        Encode text to base64 for email attachment.

        Args:
            text: Plain text string

        Returns:
            Base64 encoded string
        """
        import base64
        return base64.b64encode(text.encode('utf-8')).decode('utf-8')

    def _build_enhanced_email_body(
        self,
        summary_html: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Optional[Dict[str, Any]],
        action_items_html: Optional[str],
        participants: Optional[List[Dict[str, str]]],
        transcript_stats: Optional[Dict[str, Any]],
        include_footer: bool = True
    ) -> str:
        """
        Build enhanced HTML email body with all 6 features:
        1. Recording link
        2. Meeting statistics
        3. Transcript attachment (handled separately)
        4. Dashboard link
        5. Action items callout
        6. Participant list

        Args:
            summary_html: Summary content (HTML)
            meeting_metadata: Meeting details
            action_items_html: Extracted action items HTML
            participants: List of participants
            transcript_stats: Transcript statistics
            include_footer: Include branding footer

        Returns:
            Complete HTML email body
        """
        subject = meeting_metadata.get("subject", "Meeting")
        organizer = meeting_metadata.get("organizer_name", "Unknown")
        start_time = meeting_metadata.get("start_time", "")
        duration = meeting_metadata.get("duration_minutes", 0)
        join_url = meeting_metadata.get("join_url", "")
        recording_url = meeting_metadata.get("recording_url", "")
        recording_sharepoint_url = meeting_metadata.get("recording_sharepoint_url", "")
        transcript_sharepoint_url = meeting_metadata.get("transcript_sharepoint_url", "")
        meeting_id = meeting_metadata.get("meeting_id", "")
        dashboard_url = f"http://localhost:8000/meetings/{meeting_id}" if meeting_id else ""

        # Extract enhanced summary data
        action_items = enhanced_summary_data.get("action_items", []) if enhanced_summary_data else []
        decisions = enhanced_summary_data.get("decisions", []) if enhanced_summary_data else []
        topics = enhanced_summary_data.get("topics", []) if enhanced_summary_data else []
        highlights = enhanced_summary_data.get("highlights", []) if enhanced_summary_data else []

        # Get stats
        word_count = transcript_stats.get("word_count", 0) if transcript_stats else 0
        speaker_count = transcript_stats.get("speaker_count", 0) if transcript_stats else 0
        participant_count = meeting_metadata.get("participant_count", 0)

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

        # Build HTML
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f9f9f9;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header {{
            border-bottom: 3px solid #0078d4;
            padding-bottom: 15px;
            margin-bottom: 25px;
        }}
        .header h1 {{
            margin: 0;
            color: #0078d4;
            font-size: 24px;
        }}
        .meeting-info {{
            background: #f5f5f5;
            padding: 15px;
            border-left: 4px solid #0078d4;
            margin-bottom: 25px;
            border-radius: 4px;
        }}
        .meeting-info p {{
            margin: 5px 0;
        }}
        .stats-box {{
            display: flex;
            justify-content: space-around;
            background: #e8f4fd;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 25px;
        }}
        .stat {{
            text-align: center;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #0078d4;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }}
        .action-items-callout {{
            background: #fff9e6;
            border-left: 4px solid #ffb900;
            padding: 15px;
            margin: 25px 0;
            border-radius: 4px;
        }}
        .action-items-callout h3 {{
            margin-top: 0;
            color: #d83b01;
        }}
        .buttons {{
            margin: 25px 0;
            text-align: center;
        }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            margin: 5px;
            background: #0078d4;
            color: white !important;
            text-decoration: none;
            border-radius: 4px;
            font-weight: bold;
        }}
        .button-secondary {{
            background: #106ebe;
        }}
        .summary {{
            margin-bottom: 30px;
        }}
        .summary h2 {{
            color: #0078d4;
            border-bottom: 2px solid #e1e1e1;
            padding-bottom: 8px;
        }}
        .summary ul {{
            padding-left: 20px;
        }}
        .summary li {{
            margin: 8px 0;
        }}
        .participants {{
            background: #f5f5f5;
            padding: 15px;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .participants h3 {{
            margin-top: 0;
            color: #333;
        }}
        .participant {{
            padding: 5px 0;
        }}
        .organizer {{
            font-weight: bold;
            color: #0078d4;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e1e1e1;
            font-size: 12px;
            color: #666;
            text-align: center;
        }}
        code {{
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📝 Meeting Summary</h1>
        </div>

        <div class="meeting-info">
            <p><strong>Meeting:</strong> {subject}</p>
            <p><strong>Organizer:</strong> {organizer}</p>
            <p><strong>Date:</strong> {start_time_formatted}</p>
            <p><strong>Duration:</strong> {duration} minutes</p>
        </div>

        <!-- FEATURE 2: Meeting Statistics -->
        <div class="stats-box">
            <div class="stat">
                <div class="stat-value">{duration}</div>
                <div class="stat-label">Minutes</div>
            </div>
            <div class="stat">
                <div class="stat-value">{participant_count}</div>
                <div class="stat-label">Participants</div>
            </div>
            <div class="stat">
                <div class="stat-value">{speaker_count}</div>
                <div class="stat-label">Speakers</div>
            </div>
            <div class="stat">
                <div class="stat-value">{word_count:,}</div>
                <div class="stat-label">Words</div>
            </div>
        </div>

        <!-- FEATURE 1 & 4: SharePoint Links (Secure) -->
        <div class="buttons">
"""

        # Link to Teams meeting - users can access transcript & recording through meeting recap
        if join_url:
            html += f'            <a href="{join_url}" class="button">📝 View Meeting in Teams</a>\n'
            html += f'            <p style="font-size: 12px; color: #666; margin-top: 8px;">Access transcript, recording, and chat through Teams meeting recap</p>\n'

        html += """        </div>

"""

        # FEATURE 5: Enhanced Action Items with Structured Data
        if action_items:
            html += """        <div class="action-items-callout">
            <h3>✅ Action Items</h3>
            <ul>
"""
            for item in action_items:
                description = item.get("description", "")
                assignee = item.get("assignee", "Unassigned")
                deadline = item.get("deadline", "Not specified")
                timestamp = item.get("timestamp", "")

                html += f"""                <li>
                    <strong>{description}</strong><br>
"""
                if assignee and assignee != "Unassigned":
                    html += f"""                    👤 Assigned to: {assignee}<br>
"""
                if deadline and deadline != "Not specified":
                    html += f"""                    📅 Due: {deadline}<br>
"""
                if timestamp:
                    html += f"""                    🕐 Mentioned at: {timestamp}<br>
"""
                html += """                </li>
"""
            html += """            </ul>
        </div>

"""
        elif action_items_html and "None recorded" not in action_items_html:
            # Fallback to old format if no structured data
            html += f"""        <div class="action-items-callout">
            <h3>⚡ Action Items</h3>
            {action_items_html}
        </div>

"""

        # NEW: Key Decisions Section
        if decisions:
            html += """        <div class="decisions-section" style="background: #e8f5e8; border-left: 4px solid #4caf50; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0; color: #2e7d32;">🎯 Key Decisions</h3>
            <ul>
"""
            for decision in decisions:
                decision_text = decision.get("decision", "")
                reasoning = decision.get("reasoning", "")
                participants_str = decision.get("participants", "")
                timestamp = decision.get("timestamp", "")

                html += f"""                <li>
                    <strong>{decision_text}</strong><br>
"""
                if reasoning:
                    html += f"""                    <em>Why: {reasoning}</em><br>
"""
                if participants_str:
                    html += f"""                    👥 Participants: {participants_str}<br>
"""
                if timestamp:
                    html += f"""                    🕐 {timestamp}<br>
"""
                html += """                </li>
"""
            html += """            </ul>
        </div>

"""

        # NEW: Meeting Highlights Section
        if highlights:
            html += """        <div class="highlights-section" style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0; color: #f57c00;">⭐ Key Moments</h3>
            <ul>
"""
            for highlight in highlights:
                title = highlight.get("title", "")
                why_important = highlight.get("why_important", "")
                timestamp = highlight.get("timestamp", "")
                highlight_type = highlight.get("type", "")

                # Create recording link with timestamp if available
                recording_link_final = recording_sharepoint_url or recording_url
                if recording_link_final and timestamp:
                    # Parse MM:SS to seconds for video URL fragment
                    try:
                        parts = timestamp.split(":")
                        seconds = int(parts[0]) * 60 + int(parts[1])
                        timestamp_link = f'<a href="{recording_link_final}#t={seconds}">{timestamp}</a>'
                    except:
                        timestamp_link = timestamp
                else:
                    timestamp_link = timestamp

                html += f"""                <li>
                    <strong>{title}</strong> ({timestamp_link})<br>
                    <em>{why_important}</em>
                </li>
"""
            html += """            </ul>
        </div>

"""

        # NEW: Meeting Topics/Agenda Section
        if topics:
            html += """        <div class="topics-section" style="background: #f3e5f5; border-left: 4px solid #9c27b0; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0; color: #6a1b9a;">📋 Meeting Agenda</h3>
"""
            for topic in topics:
                topic_name = topic.get("topic", "")
                duration = topic.get("duration", "")
                summary_text = topic.get("summary", "")
                speakers = topic.get("speakers", "")

                html += f"""            <div style="margin-bottom: 15px;">
                <strong>{topic_name}</strong> <span style="color: #666;">({duration})</span><br>
"""
                if speakers:
                    html += f"""                <small>Speakers: {speakers}</small><br>
"""
                if summary_text:
                    html += f"""                <p style="margin: 5px 0;">{summary_text}</p>
"""
                html += """            </div>
"""
            html += """        </div>

"""

        html += f"""        <div class="summary">
            <h2 style="color: #0078d4; border-bottom: 2px solid #e1e1e1; padding-bottom: 8px;">📝 Full Summary</h2>
            {summary_html}
        </div>
"""

        # FEATURE 6: Participant List
        if participants and len(participants) > 0:
            html += """        <div class="participants">
            <h3>👥 Participants</h3>
"""
            organizer_email = meeting_metadata.get("organizer_email", "")
            for p in participants:
                email = p.get("email", "")
                name = p.get("display_name", email)
                is_org = email.lower() == organizer_email.lower()
                role = "Organizer" if is_org else p.get("role", "Attendee")
                css_class = "organizer" if is_org else ""
                html += f'            <div class="participant {css_class}">{name} ({role})</div>\n'

            html += """        </div>
"""

        if include_footer:
            html += """        <div class="footer">
            <p>This summary was automatically generated by AI.</p>
            <p>🔗 Access transcript and recording via SharePoint links above (respects Teams permissions)</p>
            <p>💬 <strong>Manage your preferences:</strong> Reply "@meeting notetaker no emails" in the Teams chat to opt out</p>
            <p style="color: #999; font-size: 10px;">
                🤖 Generated with <a href="https://claude.com/claude-code">Claude Code</a> | Powered by Teams Meeting Notetaker
            </p>
        </div>
"""

        html += """    </div>
</body>
</html>"""

        return html

    def send_personalized_summary(
        self,
        from_email: str,
        to_email: str,
        subject: str,
        summary_markdown: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Dict[str, Any],
        participants: Optional[List[Dict[str, str]]] = None,
        transcript_stats: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Send personalized meeting summary email to a specific user.

        Filters mentions and action items to show only items relevant to the recipient.

        Args:
            from_email: Sender email
            to_email: Recipient email (single user)
            subject: Email subject
            summary_markdown: Full meeting summary
            meeting_metadata: Meeting details
            enhanced_summary_data: Enhanced summary with mentions and action items
            participants: Optional participant list
            transcript_stats: Optional transcript statistics

        Returns:
            Message ID if sent successfully

        Raises:
            EmailSendError: If sending fails
        """
        try:
            logger.info(f"Sending personalized summary to {to_email}")

            # Extract user's mentions and action items
            user_mentions = self._filter_mentions_for_user(
                enhanced_summary_data.get("mentions", []),
                to_email
            )
            user_action_items = self._filter_action_items_for_user(
                enhanced_summary_data.get("action_items", []),
                to_email
            )

            # Convert markdown to HTML
            summary_html = markdown2.markdown(
                summary_markdown,
                extras=["tables", "fenced-code-blocks", "code-friendly", "break-on-newline", "cuddled-lists", "header-ids"]
            )

            # Build personalized email body
            body_html = self._build_personalized_email_body(
                summary_html,
                meeting_metadata,
                enhanced_summary_data,
                user_mentions,
                user_action_items,
                participants,
                transcript_stats,
                to_email
            )

            # Send email
            message_id = self.send_email(
                from_email=from_email,
                to_emails=[to_email],
                subject=f"Your Personalized Meeting Summary: {meeting_metadata.get('subject', 'Meeting')}",
                body_html=body_html,
                importance="normal"
            )

            logger.info(f"✓ Sent personalized summary to {to_email} (message: {message_id})")
            return message_id

        except Exception as e:
            logger.error(f"Failed to send personalized summary to {to_email}: {e}", exc_info=True)
            raise EmailSendError(f"Failed to send personalized summary: {e}")

    def _filter_mentions_for_user(self, mentions: List[Dict[str, Any]], user_email: str) -> List[Dict[str, Any]]:
        """Filter mentions to only those relevant to a specific user."""
        user_name = user_email.split("@")[0].replace(".", " ").title()

        return [
            m for m in mentions
            if m.get("person", "").lower() in [user_email.lower(), user_name.lower()]
            or user_email.split("@")[0].lower() in m.get("person", "").lower()
        ]

    def _filter_action_items_for_user(self, action_items: List[Dict[str, Any]], user_email: str) -> List[Dict[str, Any]]:
        """Filter action items to only those assigned to a specific user."""
        user_name = user_email.split("@")[0].replace(".", " ").title()

        return [
            item for item in action_items
            if item.get("assignee", "").lower() in [user_email.lower(), user_name.lower()]
            or user_email.split("@")[0].lower() in item.get("assignee", "").lower()
        ]

    def _build_personalized_email_body(
        self,
        summary_html: str,
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Dict[str, Any],
        user_mentions: List[Dict[str, Any]],
        user_action_items: List[Dict[str, Any]],
        participants: Optional[List[Dict[str, str]]],
        transcript_stats: Optional[Dict[str, Any]],
        user_email: str
    ) -> str:
        """Build personalized HTML email body with user-specific highlights first."""

        subject = meeting_metadata.get("subject", "Meeting")
        organizer = meeting_metadata.get("organizer_name", "Unknown")
        start_time = meeting_metadata.get("start_time", "")
        duration = meeting_metadata.get("duration_minutes", 0)
        recording_sharepoint_url = meeting_metadata.get("recording_sharepoint_url", "")
        transcript_sharepoint_url = meeting_metadata.get("transcript_sharepoint_url", "")
        meeting_id = meeting_metadata.get("meeting_id", "")
        dashboard_url = f"http://localhost:8000/meetings/{meeting_id}" if meeting_id else ""

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

        # Build HTML
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f9f9f9;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header {{
            border-bottom: 3px solid #0078d4;
            padding-bottom: 15px;
            margin-bottom: 25px;
        }}
        .header h1 {{
            margin: 0;
            color: #0078d4;
            font-size: 24px;
        }}
        .personal-highlight {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 25px;
        }}
        .personal-highlight h2 {{
            margin-top: 0;
            color: white;
        }}
        .personal-section {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        .buttons {{
            margin: 25px 0;
            text-align: center;
        }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            margin: 5px;
            background: #0078d4;
            color: white !important;
            text-decoration: none;
            border-radius: 4px;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>👤 Your Personalized Meeting Summary</h1>
        </div>

        <div class="personal-highlight">
            <h2>🎯 What's Relevant to You</h2>
            <p>This email highlights your mentions, action items, and participation in the meeting.</p>
        </div>
"""

        # YOUR MENTIONS SECTION
        if user_mentions:
            html += """        <div class="personal-section">
            <h3>👤 You Were Mentioned</h3>
            <ul>
"""
            for mention in user_mentions:
                mentioned_by = mention.get("mentioned_by", "Someone")
                context = mention.get("context", "")
                timestamp = mention.get("timestamp", "")
                mention_type = mention.get("type", "")

                html += f"""                <li>
                    <strong>{mentioned_by}</strong> at {timestamp}:<br>
                    "{context}"
"""
                if mention_type == "action_assignment":
                    html += """                    <br><span style="color: #d32f2f;">⚠️ Action required</span>
"""
                html += """                </li>
"""
            html += """            </ul>
        </div>

"""

        # YOUR ACTION ITEMS SECTION
        if user_action_items:
            html += """        <div class="personal-section">
            <h3>✅ Your Action Items</h3>
            <ul>
"""
            for item in user_action_items:
                description = item.get("description", "")
                deadline = item.get("deadline", "Not specified")
                context = item.get("context", "")
                timestamp = item.get("timestamp", "")

                # Create recording link with timestamp
                recording_link = recording_sharepoint_url
                if recording_link and timestamp:
                    try:
                        parts = timestamp.split(":")
                        seconds = int(parts[0]) * 60 + int(parts[1])
                        watch_link = f'<a href="{recording_link}#t={seconds}">🎥 Watch when assigned ({timestamp})</a>'
                    except:
                        watch_link = f"🕐 {timestamp}"
                else:
                    watch_link = f"🕐 {timestamp}" if timestamp else ""

                html += f"""                <li>
                    <strong>{description}</strong><br>
"""
                if deadline and deadline != "Not specified":
                    html += f"""                    📅 Deadline: {deadline}<br>
"""
                if context:
                    html += f"""                    <em>{context}</em><br>
"""
                if watch_link:
                    html += f"""                    {watch_link}<br>
"""
                html += """                </li>
"""
            html += """            </ul>
        </div>

"""

        # Links
        html += """        <div class="buttons">
"""
        if recording_sharepoint_url:
            html += f'            <a href="{recording_sharepoint_url}" class="button">🎥 Watch Recording</a>\n'
        if transcript_sharepoint_url:
            html += f'            <a href="{transcript_sharepoint_url}" class="button">📄 View Transcript</a>\n'
        if dashboard_url:
            html += f'            <a href="{dashboard_url}" class="button">📊 Dashboard</a>\n'
        html += """        </div>

        <hr style="margin: 30px 0; border: none; border-top: 2px solid #e1e1e1;">

        <h2 style="color: #0078d4;">📝 Full Meeting Summary</h2>
        <p><em>For complete context, here's the full summary for all participants:</em></p>
"""

        # Include full summary for context
        html += f"""        <div class="summary">
            {summary_html}
        </div>

        <div class="footer" style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e1e1e1; font-size: 12px; color: #666; text-align: center;">
            <p>This personalized summary was generated just for you.</p>
            <p>💬 Reply "@meeting notetaker no emails" in Teams to opt out of future summaries</p>
            <p style="color: #999; font-size: 10px;">
                🤖 Generated with Claude Code | Powered by Teams Meeting Notetaker
            </p>
        </div>

    </div>
</body>
</html>"""

        return html
