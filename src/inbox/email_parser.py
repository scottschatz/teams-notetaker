"""
Email Command Parser

Parses incoming emails to detect user commands:
- Subscribe: User wants to receive meeting summaries
- Unsubscribe: User wants to stop receiving summaries
- Feedback: User is providing feedback or requesting features
"""

import re
import logging
from enum import Enum
from typing import Optional, Tuple
from dataclasses import dataclass


logger = logging.getLogger(__name__)


class EmailCommandType(Enum):
    """Types of commands that can be received via email."""
    SUBSCRIBE = "subscribe"          # Opt in to meeting summaries
    UNSUBSCRIBE = "unsubscribe"      # Opt out of meeting summaries
    FEEDBACK = "feedback"            # General feedback or feature request
    SUMMARY_REQUEST = "summary_request"  # Request specific meeting summary (future)
    UNKNOWN = "unknown"              # Could not determine command type


@dataclass
class ParsedEmailCommand:
    """Result of parsing an email for commands."""
    command_type: EmailCommandType
    sender_email: str
    sender_name: str
    subject: str
    body: str
    confidence: float  # 0.0 to 1.0, how confident we are in the command type


class EmailCommandParser:
    """
    Parses incoming emails to detect commands.

    Command detection is based on subject line keywords with body as fallback.
    """

    # Keywords that indicate subscribe intent (order matters - more specific first)
    SUBSCRIBE_PATTERNS = [
        r'\bresubscribe\b',
        r'\bsubscribe\b',
        r'\bopt[\s-]?in\b',
        r'\benable\b',
        r'\bstart\s+sending\b',
        r'\bsign\s+me\s+up\b',
        r'\byes\s+please\b',
    ]

    # Keywords that indicate unsubscribe intent
    UNSUBSCRIBE_PATTERNS = [
        r'\bunsubscribe\b',
        r'\bopt[\s-]?out\b',
        r'\bdisable\b',
        r'\bstop\s+sending\b',
        r'\bstop\s+emails\b',
        r'\bremove\s+me\b',
        r'\bno\s+more\b',
    ]

    # Keywords that indicate feedback
    FEEDBACK_PATTERNS = [
        r'\bfeedback\b',
        r'\bsuggestion\b',
        r'\bfeature\s+request\b',
        r'\bbug\s+report\b',
        r'\bissue\b',
        r'\bproblem\b',
        r'\bimprovement\b',
    ]

    def parse_email(
        self,
        sender_email: str,
        sender_name: str,
        subject: str,
        body: str
    ) -> ParsedEmailCommand:
        """
        Parse an email to determine command type.

        Args:
            sender_email: Email address of sender
            sender_name: Display name of sender
            subject: Email subject line
            body: Email body text (plain text)

        Returns:
            ParsedEmailCommand with detected command type and confidence
        """
        # Normalize inputs
        subject_lower = subject.lower().strip()
        body_lower = body.lower().strip() if body else ""

        # Check subject first (higher confidence)
        command_type, confidence = self._detect_command_type(subject_lower, is_subject=True)

        # If unknown from subject, check body (lower confidence)
        if command_type == EmailCommandType.UNKNOWN and body_lower:
            command_type, body_confidence = self._detect_command_type(body_lower, is_subject=False)
            confidence = body_confidence * 0.7  # Lower confidence for body detection

        logger.debug(
            f"Parsed email from {sender_email}: command={command_type.value}, "
            f"confidence={confidence:.2f}, subject='{subject[:50]}...'"
        )

        return ParsedEmailCommand(
            command_type=command_type,
            sender_email=sender_email.lower(),
            sender_name=sender_name,
            subject=subject,
            body=body,
            confidence=confidence
        )

    def _detect_command_type(self, text: str, is_subject: bool) -> Tuple[EmailCommandType, float]:
        """
        Detect command type from text.

        Args:
            text: Lowercased text to analyze
            is_subject: True if this is the subject line (higher base confidence)

        Returns:
            Tuple of (command_type, confidence)
        """
        base_confidence = 0.9 if is_subject else 0.6

        # Check unsubscribe first (more critical to get right)
        for pattern in self.UNSUBSCRIBE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EmailCommandType.UNSUBSCRIBE, base_confidence

        # Check subscribe
        for pattern in self.SUBSCRIBE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EmailCommandType.SUBSCRIBE, base_confidence

        # Check feedback
        for pattern in self.FEEDBACK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EmailCommandType.FEEDBACK, base_confidence

        # No pattern matched
        return EmailCommandType.UNKNOWN, 0.0

    def is_auto_reply(self, subject: str, body: str, headers: dict = None) -> bool:
        """
        Check if email is an auto-reply that should be ignored.

        Args:
            subject: Email subject
            body: Email body
            headers: Email headers (if available)

        Returns:
            True if this looks like an auto-reply
        """
        subject_lower = subject.lower()

        # Common auto-reply indicators
        auto_reply_subjects = [
            'out of office',
            'automatic reply',
            'auto-reply',
            'autoreply',
            'away from',
            'on vacation',
            'delivery status',
            'undeliverable',
            'mail delivery failed',
            'returned mail',
        ]

        for indicator in auto_reply_subjects:
            if indicator in subject_lower:
                return True

        # Check headers if available
        if headers:
            # Auto-Submitted header
            if headers.get('Auto-Submitted', '').lower() not in ['no', '']:
                return True
            # X-Auto-Response-Suppress
            if headers.get('X-Auto-Response-Suppress'):
                return True

        return False
