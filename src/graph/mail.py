"""
Microsoft Graph API - Email Sending

Sends meeting summary emails via Microsoft Graph API sendMail.
Supports HTML formatting and multiple recipients.
"""

import logging
from typing import List, Dict, Any, Optional
import markdown2

from ..graph.client import GraphAPIClient
from ..core.exceptions import EmailSendError


logger = logging.getLogger(__name__)


class EmailSender:
    """
    Sends emails via Microsoft Graph API.

    Uses /users/{userId}/sendMail endpoint to send emails on behalf of a user
    or /users/{userId}/messages + /send for more control.

    Usage:
        client = GraphAPIClient(config)
        sender = EmailSender(client)

        sender.send_meeting_summary(
            from_email="noreply@townsquaremedia.com",
            to_emails=["user1@example.com", "user2@example.com"],
            subject="Meeting Summary: Weekly Sync",
            summary_markdown="## Summary\n\n...",
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
        cc_emails: Optional[List[str]] = None,
        include_footer: bool = True
    ) -> str:
        """
        Send meeting summary email.

        Args:
            from_email: Sender email (must have sendMail permission)
            to_emails: List of recipient emails
            subject: Email subject
            summary_markdown: Summary in markdown format
            meeting_metadata: Meeting details (organizer, date, etc.)
            cc_emails: Optional CC recipients
            include_footer: Add standard footer with branding

        Returns:
            Message ID if sent successfully

        Raises:
            EmailSendError: If sending fails
        """
        try:
            logger.info(f"Sending meeting summary to {len(to_emails)} recipient(s)")

            # Convert markdown to HTML
            summary_html = markdown2.markdown(
                summary_markdown,
                extras=["tables", "fenced-code-blocks", "code-friendly"]
            )

            # Build email body
            body_html = self._build_email_body(
                summary_html,
                meeting_metadata,
                include_footer
            )

            # Send email
            message_id = self.send_email(
                from_email=from_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                subject=subject,
                body_html=body_html,
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

            # Send email using sendMail endpoint
            endpoint = f"/users/{from_email}/sendMail"
            payload = {
                "message": message,
                "saveToSentItems": True
            }

            logger.debug(f"Sending email from {from_email} to {len(to_emails)} recipient(s)")

            self.client.post(endpoint, json=payload)

            # Graph API sendMail returns 202 Accepted with no body
            # Generate a message ID for tracking (Graph doesn't return one for sendMail)
            import uuid
            message_id = f"<{uuid.uuid4()}@townsquaremedia.com>"

            return message_id

        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            raise EmailSendError(f"Email send failed: {e}")

    def _build_email_body(
        self,
        summary_html: str,
        meeting_metadata: Dict[str, Any],
        include_footer: bool = True
    ) -> str:
        """
        Build complete email body with header, summary, and footer.

        Args:
            summary_html: Summary content in HTML
            meeting_metadata: Meeting details
            include_footer: Include branding footer

        Returns:
            Complete HTML email body
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
        }}
        .meeting-info p {{
            margin: 5px 0;
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
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e1e1e1;
            font-size: 12px;
            color: #666;
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
    <div class="header">
        <h1>📝 Meeting Summary</h1>
    </div>

    <div class="meeting-info">
        <p><strong>Meeting:</strong> {subject}</p>
        <p><strong>Organizer:</strong> {organizer}</p>
        <p><strong>Date:</strong> {start_time_formatted}</p>
        <p><strong>Duration:</strong> {duration} minutes</p>
    </div>

    <div class="summary">
        {summary_html}
    </div>
"""

        if include_footer:
            html += """
    <div class="footer">
        <p>
            This summary was automatically generated by the Teams Meeting Transcript Summarizer
            using AI (Claude by Anthropic).
        </p>
        <p>
            If you have questions or feedback, please contact your IT administrator.
        </p>
    </div>
"""

        html += """
</body>
</html>
"""

        return html

    def send_test_email(self, from_email: str, to_email: str) -> bool:
        """
        Send a test email to verify configuration.

        Args:
            from_email: Sender email
            to_email: Recipient email

        Returns:
            True if sent successfully
        """
        try:
            logger.info(f"Sending test email from {from_email} to {to_email}")

            message_id = self.send_email(
                from_email=from_email,
                to_emails=[to_email],
                subject="Test Email from Teams Notetaker",
                body_html="""
                <html>
                <body>
                    <h2>✓ Email Configuration Test</h2>
                    <p>This is a test email from the Teams Meeting Transcript Summarizer.</p>
                    <p>If you received this email, the email configuration is working correctly!</p>
                </body>
                </html>
                """
            )

            logger.info(f"✓ Test email sent successfully (message_id: {message_id})")
            return True

        except Exception as e:
            logger.error(f"✗ Test email failed: {e}")
            return False
