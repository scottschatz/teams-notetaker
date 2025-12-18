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
        invitees: Optional[List[Dict[str, str]]] = None,
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
            invitees: Optional list of invitees who may not have attended
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
                summary_markdown,  # Pass markdown for extracting Executive Summary and Discussion Notes
                meeting_metadata,
                enhanced_summary_data,
                action_items_html,
                participants,
                transcript_stats,
                invitees,
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

    def _make_names_blue(self, text: str) -> str:
        """
        Convert markdown bold **Name** to HTML bold + blue styling.

        This ensures all participant names are consistently styled as bold AND blue
        throughout the email (action items, decisions, key moments, discussion notes).

        Args:
            text: Text with markdown bold syntax (**Name**)

        Returns:
            Text with HTML bold+blue styling
        """
        import re
        # Replace **Name** with <strong style="color: #0078d4; font-weight: 700;">Name</strong>
        # This pattern matches **any text** (markdown bold syntax)
        pattern = r'\*\*([^*]+)\*\*'
        replacement = r'<strong style="color: #0078d4; font-weight: 700;">\1</strong>'
        return re.sub(pattern, replacement, text)

    def _build_enhanced_email_body(
        self,
        summary_html: str,
        summary_markdown: str,  # Markdown version for extracting sections
        meeting_metadata: Dict[str, Any],
        enhanced_summary_data: Optional[Dict[str, Any]],
        action_items_html: Optional[str],
        participants: Optional[List[Dict[str, str]]],
        transcript_stats: Optional[Dict[str, Any]],
        invitees: Optional[List[Dict[str, str]]] = None,
        include_footer: bool = True
    ) -> str:
        """
        Build enhanced HTML email body with all features:
        1. Recording link
        2. Meeting statistics
        3. Transcript attachment (handled separately)
        4. Dashboard link
        5. Action items callout
        6. Participant list (Top Speakers, Also Present, Invited)

        Args:
            summary_html: Summary content (HTML)
            meeting_metadata: Meeting details
            action_items_html: Extracted action items HTML
            participants: List of participants who attended
            transcript_stats: Transcript statistics
            invitees: List of invitees who may not have attended
            include_footer: Include branding footer

        Returns:
            Complete HTML email body
        """
        subject = meeting_metadata.get("subject", "Meeting")
        organizer = meeting_metadata.get("organizer_name") or meeting_metadata.get("organizer", "Unknown")
        start_time = meeting_metadata.get("start_time", "")
        scheduled_duration = meeting_metadata.get("duration_minutes", 0)  # From calendar
        join_url = meeting_metadata.get("join_url", "")
        chat_id = meeting_metadata.get("chat_id", "")
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
        invited_count = meeting_metadata.get("invited_count")

        # Use actual duration from transcript stats (v2.1 feature)
        actual_duration = transcript_stats.get("actual_duration_minutes", 0) if transcript_stats else 0
        duration = actual_duration if actual_duration > 0 else scheduled_duration

        # Build duration display (only show actual duration, not scheduled)
        duration_display = f"{duration} minutes"

        # Build participant display with actual vs invited if different
        participant_display = str(participant_count)
        if invited_count and participant_count != invited_count:
            participant_display = f"{participant_count} <span style='color: #666; font-size: 0.9em;'>({invited_count} invited)</span>"

        # Format start time
        if isinstance(start_time, str):
            # If already formatted with timezone (contains EST/EDT), use as-is
            if " EST" in start_time or " EDT" in start_time:
                start_time_formatted = start_time
            else:
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
        .summary strong {{
            color: #0078d4;
            font-weight: 700;
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

        <!-- Meeting Statistics -->
        <div class="stats-box">
            <div class="stat">
                <div class="stat-value">{duration_display}</div>
                <div class="stat-label">Duration</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(participants) if participants else participant_count}</div>
                <div class="stat-label">Speakers</div>
            </div>
            <div class="stat">
                <div class="stat-value">{word_count:,}</div>
                <div class="stat-label">Words Spoken</div>
            </div>
        </div>

"""

        # v2.1: Attendees & Participation (HYBRID: top 3 speakers detailed, others simplified)
        speaker_details = transcript_stats.get("speaker_details", []) if transcript_stats else []

        # Create speaker lookup for quick access
        speaker_map = {}
        for speaker in speaker_details:
            name = speaker.get('name', '').strip()
            speaker_map[name.lower()] = speaker

        if participants and len(participants) > 0:
            # Sort participants by speaking time (most active first)
            speakers = []
            non_speakers = []
            for p in participants:
                name = p.get("display_name", p.get("email", ""))
                speaker_data = speaker_map.get(name.lower())
                if speaker_data:
                    p['_speaker_data'] = speaker_data
                    speakers.append(p)
                else:
                    non_speakers.append(p)

            # Sort speakers by duration (descending)
            speakers.sort(key=lambda x: x['_speaker_data'].get('duration_minutes', 0), reverse=True)

            html += """        <div class="attendees-section" style="background: #f5f5f5; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0;">👥 Attendees & Participation</h3>
"""

            # Top 3 speakers with full stats
            top_speakers = speakers[:3]
            other_attendees = speakers[3:] + non_speakers

            organizer_email = meeting_metadata.get("organizer_email") or ""

            if top_speakers:
                html += """            <div style="margin-bottom: 15px;"><strong>Top Speakers:</strong></div>
"""
                for p in top_speakers:
                    email = p.get("email") or ""
                    name = p.get("display_name", email)
                    # Handle None values safely
                    is_org = (email.lower() == organizer_email.lower()) if email and organizer_email else False
                    role = " (Organizer)" if is_org else ""
                    job_title = p.get("job_title", "")
                    photo_base64 = p.get("photo_base64", "")
                    speaker_data = p.get('_speaker_data', {})

                    # Show speaker with photo, name, title, and FULL stats
                    html += f"""            <div style="display: flex; align-items: center; margin-bottom: 15px;">
"""
                    # Profile photo (48x48 circular) if available
                    if photo_base64:
                        html += f"""                <img src="data:image/jpeg;base64,{photo_base64}"
                         style="width: 48px; height: 48px; border-radius: 50%; margin-right: 12px; flex-shrink: 0;"
                         alt="{name}" />
"""
                    else:
                        # Placeholder avatar with initials if no photo
                        initials = ''.join([word[0].upper() for word in name.split()[:2]])
                        html += f"""                <div style="width: 48px; height: 48px; border-radius: 50%; background: #0078d4;
                                color: white; display: flex; align-items: center; justify-content: center;
                                margin-right: 12px; font-weight: 700; font-size: 18px; flex-shrink: 0;">
                        {initials}
                    </div>
"""

                    # Name, title, and FULL speaking stats
                    html += f"""                <div style="flex-grow: 1;">
                        <strong style="color: #0078d4; font-weight: 700;">{name}{role}</strong><br>
"""
                    if job_title:
                        html += f"""                    <span style="color: #666; font-size: 0.9em;">{job_title}</span><br>
"""
                    duration_min = speaker_data.get('duration_minutes', 0)
                    words = speaker_data.get('words', 0)
                    percentage = speaker_data.get('percentage', 0)
                    # NOTE: Removed 🎤 emoji, kept full stats for top 3
                    html += f"""                    <span style="color: #666; font-size: 0.9em;">Spoke: {duration_min} min ({percentage}%) | {words:,} words</span>
                    </div>
                </div>
"""

            # Other attendees (with profile pictures, no speaking stats)
            # If >5 attendees, show first 5 with photos, rest in compact format
            if other_attendees:
                show_with_photos = other_attendees[:5]
                show_compact = other_attendees[5:] if len(other_attendees) > 5 else []

                html += """            <div style="margin-top: 20px; margin-bottom: 10px;"><strong>Also Present:</strong></div>
"""
                # Show first 5 (or all if <=5) with full detail + photos
                for p in show_with_photos:
                    email = p.get("email") or ""
                    name = p.get("display_name", email)
                    is_org = (email.lower() == organizer_email.lower()) if email and organizer_email else False
                    role = " (Organizer)" if is_org else ""
                    job_title = p.get("job_title", "")
                    photo_base64 = p.get("photo_base64", "")

                    # Show attendee with photo, name, and title (no stats)
                    html += f"""            <div style="display: flex; align-items: center; margin-bottom: 10px;">
"""
                    # Profile photo (32x32 circular) if available
                    if photo_base64:
                        html += f"""                <img src="data:image/jpeg;base64,{photo_base64}"
                         style="width: 32px; height: 32px; border-radius: 50%; margin-right: 10px; flex-shrink: 0;"
                         alt="{name}" />
"""
                    else:
                        # Placeholder avatar with initials if no photo
                        initials = ''.join([word[0].upper() for word in name.split()[:2]])
                        html += f"""                <div style="width: 32px; height: 32px; border-radius: 50%; background: #0078d4;
                                color: white; display: flex; align-items: center; justify-content: center;
                                margin-right: 10px; font-weight: 700; font-size: 14px; flex-shrink: 0;">
                        {initials}
                    </div>
"""

                    # Name and title
                    html += f"""                <div>
                        <strong style="color: #0078d4; font-weight: 700;">{name}{role}</strong>"""
                    if job_title:
                        html += f""" - <span style="color: #666; font-size: 0.9em;">{job_title}</span>"""
                    html += """
                    </div>
                </div>
"""

                # If >5 attendees, show remaining in compact format (just names, comma-separated)
                if show_compact:
                    compact_names = []
                    for p in show_compact:
                        name = p.get("display_name", p.get("email", ""))
                        email = p.get("email") or ""
                        is_org = (email.lower() == organizer_email.lower()) if email and organizer_email else False
                        if is_org:
                            name += " (Organizer)"
                        compact_names.append(name)

                    html += f"""            <div style="margin-top: 10px; padding: 10px; background: #f9f9f9; border-radius: 4px; font-size: 0.9em; color: #666;">
                <strong>+{len(show_compact)} more:</strong> {', '.join(compact_names)}
            </div>
"""

            # Invited section - show invitees who may not have attended
            if invitees and len(invitees) > 0:
                html += """            <div style="margin-top: 20px; margin-bottom: 10px;"><strong>Invited:</strong></div>
"""
                # Show invitees in a compact format (just names/emails)
                invitee_names = []
                for inv in invitees:
                    name = inv.get("name") or inv.get("email", "").split("@")[0]
                    invitee_names.append(name)

                html += f"""            <div style="padding: 10px; background: #fff3e0; border-radius: 4px; font-size: 0.9em; color: #666;">
                {', '.join(invitee_names)}
            </div>
"""

            html += """        </div>

"""

        # Executive Summary Section (variable length: 50-125 words based on complexity)
        # Extract executive summary from summary_markdown (first section after ## Executive Summary)
        exec_summary_text = ""
        if summary_markdown:
            import re
            # Match: ## Executive Summary followed by content until next ## heading
            match = re.search(r'##\s*Executive Summary\s*\n(.*?)(?=\n##|\Z)', summary_markdown, re.DOTALL | re.IGNORECASE)
            if match:
                exec_summary_text = match.group(1).strip()

        if exec_summary_text:
            # Apply blue+bold styling to names before converting markdown to HTML
            exec_summary_text = self._make_names_blue(exec_summary_text)
            # Convert markdown to HTML for the executive summary
            exec_summary_html = markdown2.markdown(exec_summary_text, extras=["break-on-newline"])
            html += f"""        <div style="background: #e3f2fd; padding: 15px; margin: 25px 0; border-radius: 4px; border-left: 4px solid #2196f3;">
            <h3 style="margin-top: 0;">📌 Executive Summary</h3>
            {exec_summary_html}
        </div>

"""

        # FEATURE 5: Enhanced Action Items with Structured Data (Grouped by Person)
        if action_items:
            # Group action items by assignee
            from collections import defaultdict
            items_by_person = defaultdict(list)
            for item in action_items:
                assignee = item.get("assignee", "Unassigned")
                items_by_person[assignee].append(item)

            html += """        <div class="action-items-callout">
            <h3>✅ Action Items</h3>
"""
            # Display items grouped by person
            for assignee, items in items_by_person.items():
                # Person header with bold and color (no inline emoji)
                if assignee and assignee != "Unassigned":
                    html += f"""            <h4 style="color: #0078d4; font-weight: 700; margin-top: 15px; margin-bottom: 8px;"><strong>{assignee}</strong></h4>
            <ul style="margin-top: 0;">
"""
                else:
                    html += """            <h4 style="color: #666; font-weight: 700; margin-top: 15px; margin-bottom: 8px;"><strong>Unassigned</strong></h4>
            <ul style="margin-top: 0;">
"""

                for item in items:
                    description = item.get("description", "")
                    deadline = item.get("deadline", "Not specified")

                    # Apply blue+bold styling to names in description
                    description = self._make_names_blue(description)

                    # Single-line format: description → deadline (no timestamps)
                    item_text = description
                    if deadline and deadline != "Not specified":
                        item_text += f" → {deadline}"

                    html += f"""                <li>{item_text}</li>
"""
                html += """            </ul>
"""
            html += """        </div>

"""
        elif action_items_html and "None recorded" not in action_items_html:
            # Fallback to old format if no structured data
            html += f"""        <div class="action-items-callout">
            <h3>⚡ Action Items</h3>
            {action_items_html}
        </div>

"""

        # Decisions Made Section (v2.1: simplified single-line format)
        if decisions:
            html += """        <div class="decisions-section" style="background: #e8f5e8; border-left: 4px solid #4caf50; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0; color: #2e7d32;">🎯 Decisions Made</h3>
            <ul>
"""
            for decision in decisions:
                decision_text = decision.get("decision", "")
                # Use rationale_one_line (new field from updated DECISION_PROMPT)
                # Fallback to reasoning if rationale_one_line not available
                rationale = decision.get("rationale_one_line") or decision.get("reasoning", "")

                # Apply blue+bold styling to names
                decision_text = self._make_names_blue(decision_text)
                rationale = self._make_names_blue(rationale)

                # Single-line format: Decision - Rationale
                if rationale:
                    html += f"""                <li>{decision_text} - {rationale}</li>
"""
                else:
                    html += f"""                <li>{decision_text}</li>
"""
            html += """            </ul>
        </div>

"""

        # Key Numbers Section (v2.1: NEW - all financial/quantitative metrics)
        key_numbers = enhanced_summary_data.get("key_numbers", [])
        if key_numbers:
            html += """        <div style="background: #e8f5e9; padding: 15px; margin: 25px 0; border-radius: 4px; border-left: 4px solid #4caf50;">
            <h3 style="margin-top: 0; color: #2e7d32;">📊 Key Numbers</h3>
            <ul style="list-style: none; padding-left: 0;">
"""
            for number in key_numbers:
                value = number.get("value", "")
                context = number.get("context", "")
                # Apply blue+bold styling to names in context
                context = self._make_names_blue(context)
                html += f"""                <li style="margin-bottom: 8px;"><strong>{value}</strong> - {context}</li>
"""
            html += """            </ul>
        </div>

"""

        # Key Moments Section (ordered by importance, timestamp at end)
        if highlights:
            # Limit to 8 highlights (already ordered by importance from AI)
            highlights_limited = highlights[:8]

            html += """        <div class="highlights-section" style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 25px 0; border-radius: 4px;">
            <h3 style="margin-top: 0; color: #f57c00;">⚡ Key Moments</h3>
            <ul style="list-style: none; padding-left: 0;">
"""
            for highlight in highlights_limited:
                # Use 'description' field from updated HIGHLIGHTS_PROMPT
                # Fallback to 'title' for backward compatibility
                description = highlight.get("description") or highlight.get("title", "")
                timestamp = highlight.get("timestamp", "")

                # Apply blue+bold styling to names in description
                description = self._make_names_blue(description)

                # Create recording link with timestamp if available
                recording_link_final = recording_sharepoint_url or recording_url
                if recording_link_final and timestamp:
                    # Parse H:MM:SS or MM:SS to seconds for video URL fragment
                    try:
                        parts = timestamp.split(":")
                        if len(parts) == 3:  # H:MM:SS
                            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        else:  # MM:SS
                            seconds = int(parts[0]) * 60 + int(parts[1])
                        timestamp_link = f'<a href="{recording_link_final}#t={seconds}" style="font-weight: 700; color: #f57c00;">{timestamp}</a>'
                    except:
                        timestamp_link = f'<span style="font-weight: 700; color: #f57c00;">{timestamp}</span>'
                else:
                    timestamp_link = f'<span style="font-weight: 700; color: #f57c00;">{timestamp}</span>' if timestamp else ""

                # Single-line format: description (timestamp) - timestamp at end to show it's ordered by importance
                if timestamp_link:
                    html += f"""                <li style="margin-bottom: 8px;">{description} ({timestamp_link})</li>
"""
                else:
                    html += f"""                <li style="margin-bottom: 8px;">{description}</li>
"""

            # Add "See full timeline in Teams" link if chat_id available
            if chat_id:
                import urllib.parse
                encoded_chat_id = urllib.parse.quote(chat_id)
                chat_url = f"https://teams.microsoft.com/l/chat/{encoded_chat_id}/0"
                html += f"""            </ul>
            <p style="margin-top: 10px;"><a href="{chat_url}" style="color: #f57c00;">See full timeline in Teams →</a></p>
        </div>

"""
            else:
                html += """            </ul>
        </div>

"""

        # Discussion Notes Section (v2.1: consolidated narrative with thematic subheadings)
        discussion_notes_text = ""
        if summary_markdown:
            import re
            # Match: ## Discussion Notes followed by content until end or next ## heading
            match = re.search(r'##\s*Discussion Notes\s*\n(.*?)(?=\n##|\Z)', summary_markdown, re.DOTALL | re.IGNORECASE)
            if match:
                discussion_notes_text = match.group(1).strip()

        if discussion_notes_text:
            # Apply blue+bold styling to names before converting markdown to HTML
            discussion_notes_text = self._make_names_blue(discussion_notes_text)
            discussion_notes_html = markdown2.markdown(discussion_notes_text, extras=["break-on-newline"])
            # Add paragraph spacing by wrapping in a div with p styling
            html += f"""        <div style="background: #fff3e0; padding: 15px; margin: 25px 0; border-radius: 4px; border-left: 4px solid #ff9800;">
            <h3 style="margin-top: 0; color: #e65100;">📝 Discussion Notes</h3>
            <div style="line-height: 1.6;">
                {discussion_notes_html.replace('<p>', '<p style="margin-bottom: 15px;">')}
            </div>
        </div>
"""

        if include_footer:
            html += """        <div class="footer">
"""
            # Link to Teams chat - moved to footer
            if chat_id:
                # Construct Teams deep link to meeting chat
                import urllib.parse
                encoded_chat_id = urllib.parse.quote(chat_id)
                chat_url = f"https://teams.microsoft.com/l/chat/{encoded_chat_id}/0"
                html += f"""            <div style="text-align: center; margin-bottom: 30px;">
                <a href="{chat_url}" class="button" style="display: inline-block; background: #0078d4; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 600;">💬 Open Meeting Chat in Teams</a>
                <p style="font-size: 12px; color: #666; margin-top: 8px;">Access transcript, recording, files, and meeting recap in Teams</p>
            </div>
"""
            elif join_url:
                # Fallback to join URL if no chat_id
                html += f"""            <div style="text-align: center; margin-bottom: 30px;">
                <a href="{join_url}" class="button" style="display: inline-block; background: #0078d4; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 600;">📝 View Meeting in Teams</a>
            </div>
"""

            html += """            <!-- Email Preferences for Future Meetings -->
            <div style="border-top: 2px solid #e0e0e0; margin-top: 40px; padding-top: 25px;
                        background: #f9f9f9; padding: 20px; border-radius: 4px; font-size: 0.9em;">

                <h3 style="margin-top: 0; color: #333; font-size: 1.1em;">
                    📧 Email Preferences for Future Meetings
                </h3>

                <div style="margin-bottom: 15px;">
                    <strong style="color: #0078d4;">For this meeting:</strong><br>
                    <span style="color: #555;">
                        Don't want summaries for this meeting? Type in the meeting chat:<br>
                        <code style="background: white; padding: 2px 6px; border: 1px solid #ddd;
                                    border-radius: 3px; font-family: monospace; font-size: 0.95em;">
                            no emails
                        </code>
                    </span>
                </div>

                <div style="margin-bottom: 15px;">
                    <strong style="color: #0078d4;">For all meetings:</strong><br>
                    <span style="color: #555;">
                        Stop receiving all summaries - type in any meeting chat:<br>
                        <code style="background: white; padding: 2px 6px; border: 1px solid #ddd;
                                    border-radius: 3px; font-family: monospace; font-size: 0.95em;">
                            no emails for all meetings
                        </code>
                    </span>
                </div>

                <div style="margin-bottom: 15px;">
                    <strong style="color: #0078d4;">Changed your mind?</strong><br>
                    <span style="color: #555;">
                        Start receiving summaries again - type in any meeting chat:<br>
                        <code style="background: white; padding: 2px 6px; border: 1px solid #ddd;
                                    border-radius: 3px; font-family: monospace; font-size: 0.95em;">
                            enable emails
                        </code>
                    </span>
                </div>

                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

                <div style="font-size: 0.85em; color: #666;">
                    <strong>For Meeting Organizers:</strong><br>
                    <span style="color: #777;">
                        Disable distribution for a meeting:<br>
                        <code style="background: white; padding: 2px 6px; border: 1px solid #ddd;
                                    border-radius: 3px; font-family: monospace; font-size: 0.95em;">
                            @meeting notetaker disable distribution
                        </code>
                    </span>
                </div>

            </div>
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
        organizer = meeting_metadata.get("organizer_name") or meeting_metadata.get("organizer", "Unknown")
        start_time = meeting_metadata.get("start_time", "")
        duration = meeting_metadata.get("duration_minutes", 0)
        recording_sharepoint_url = meeting_metadata.get("recording_sharepoint_url", "")
        transcript_sharepoint_url = meeting_metadata.get("transcript_sharepoint_url", "")
        meeting_id = meeting_metadata.get("meeting_id", "")
        dashboard_url = f"http://localhost:8000/meetings/{meeting_id}" if meeting_id else ""

        # Format start time
        if isinstance(start_time, str):
            # If already formatted with timezone (contains EST/EDT), use as-is
            if " EST" in start_time or " EDT" in start_time:
                start_time_formatted = start_time
            else:
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

                # Only show timestamp if available
                timestamp_display = f" at {timestamp}" if timestamp else ""

                html += f"""                <li>
                    <span style="font-weight: 700; color: #000;">{mentioned_by}</span>{timestamp_display}:<br>
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
            
            <p style="color: #999; font-size: 10px;">
                🤖 Generated with Claude Code | Powered by Teams Meeting Notetaker
            </p>
        </div>

    </div>
</body>
</html>"""

        return html
