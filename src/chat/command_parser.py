"""
Chat Command Parser

Parses Teams chat messages for meeting notetaker bot commands.
Supports command detection, parameter extraction, and validation.
"""

import logging
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum


logger = logging.getLogger(__name__)


class CommandType(Enum):
    """Supported command types."""
    EMAIL_ME = "email_me"  # Send personalized email to requesting user
    EMAIL_ALL = "email_all"  # Send standard email to all participants (organizer only)
    NO_EMAILS = "no_emails"  # Opt out of THIS meeting (per-meeting preference)
    NO_EMAILS_GLOBAL = "no_emails_global"  # Opt out of ALL meetings (global preference)
    ENABLE_EMAILS = "enable_emails"  # Re-enable emails globally (opt back in)
    DISABLE_DISTRIBUTION = "disable_distribution"  # Organizer disables distribution for meeting
    ENABLE_DISTRIBUTION = "enable_distribution"  # Organizer re-enables distribution for meeting
    SUMMARIZE_AGAIN = "summarize_again"  # Re-generate summary with custom instructions
    EMOJI_REACTION = "emoji_reaction"  # 📧 emoji reaction
    UNKNOWN = "unknown"  # Unrecognized command


@dataclass
class Command:
    """
    Parsed command from chat message.

    Attributes:
        command_type: Type of command
        message_id: Teams message ID
        chat_id: Teams chat thread ID
        user_email: Email of user who sent command
        user_name: Display name of user
        parameters: Command-specific parameters
        raw_message: Original message text
        is_valid: Whether command is valid and should be processed
        error_message: Error message if command is invalid
    """
    command_type: CommandType
    message_id: str
    chat_id: str
    user_email: str
    user_name: str
    parameters: Dict[str, Any]
    raw_message: str
    is_valid: bool = True
    error_message: Optional[str] = None


class ChatCommandParser:
    """
    Parses Teams chat messages for bot commands.

    Supports:
    - @meeting notetaker <command>
    - Emoji reactions (📧)
    - Parameter extraction
    - Validation

    Usage:
        parser = ChatCommandParser()

        # Parse message
        command = parser.parse_command(
            message_text="@meeting notetaker email me",
            message_id="1234567890",
            chat_id="19:meeting_abc@thread.v2",
            user_email="user@example.com",
            user_name="John Doe"
        )

        if command and command.is_valid:
            # Process command
            ...
    """

    # Bot mention patterns
    BOT_MENTION_PATTERNS = [
        r"@meeting\s+notetaker",  # @meeting notetaker
        r"@meetingnotetaker",  # @meetingnotetaker (no space)
        r"@meeting-notetaker",  # @meeting-notetaker (hyphen)
        r"@meeting_notetaker",  # @meeting_notetaker (underscore)
    ]

    # Command patterns
    # IMPORTANT: Order matters! Check more specific patterns first (NO_EMAILS_GLOBAL before NO_EMAILS)
    COMMAND_PATTERNS = {
        CommandType.EMAIL_ME: [
            r"email\s+me",
            r"send\s+me\s+email",
            r"send\s+me\s+(a|an)\s+email",
            r"email\s+me\s+(a|an)\s+summary",
        ],
        CommandType.EMAIL_ALL: [
            r"email\s+all",
            r"email\s+everyone",
            r"send\s+to\s+all",
            r"send\s+everyone",
        ],
        # Global opt-out (check BEFORE NO_EMAILS)
        CommandType.NO_EMAILS_GLOBAL: [
            r"no\s+emails?\s+(all\s+meetings?|globally?|forever)",
            r"global\s+opt\s+out",
            r"opt\s+out\s+(all|globally?|forever)",
            r"stop\s+all\s+emails?",
            r"unsubscribe\s+(from\s+)?(all|everything|globally?)",
        ],
        # Per-meeting opt-out (default behavior)
        CommandType.NO_EMAILS: [
            r"no\s+emails?(?!\s+(all|globally?|forever))",  # Negative lookahead
            r"opt\s+out(?!\s+(all|globally?))",
            r"stop\s+emails?(?!\s+(all|globally?))",
            r"unsubscribe(?!\s+(all|everything|globally?))",
            r"don'?t\s+(send\s+me\s+)?(email|emails?)",
        ],
        # Global opt-in (re-enable emails)
        CommandType.ENABLE_EMAILS: [
            r"enable\s+emails?",
            r"opt\s+in",
            r"start\s+emails?",
            r"subscribe",
            r"re-?enable\s+emails?",
            r"turn\s+on\s+emails?",
        ],
        # Organizer: disable distribution for entire meeting
        CommandType.DISABLE_DISTRIBUTION: [
            r"disable\s+distribution",
            r"no\s+emails?\s+for\s+(anyone|everyone|this\s+meeting)",
            r"stop\s+distribution",
            r"disable\s+auto\s+send",
            r"no\s+auto\s+emails?",
        ],
        # Organizer: re-enable distribution for meeting
        CommandType.ENABLE_DISTRIBUTION: [
            r"enable\s+distribution",
            r"start\s+distribution",
            r"re-?enable\s+auto\s+send",
            r"turn\s+on\s+distribution",
        ],
        CommandType.SUMMARIZE_AGAIN: [
            r"summarize\s+again",
            r"re-?summarize",
            r"regenerate",
            r"create\s+new\s+summary",
        ],
    }

    def __init__(self):
        """Initialize command parser."""
        pass

    def parse_command(
        self,
        message_text: str,
        message_id: str,
        chat_id: str,
        user_email: str,
        user_name: str
    ) -> Optional[Command]:
        """
        Parse chat message for bot command.

        Args:
            message_text: Message text from Teams
            message_id: Teams message ID
            chat_id: Teams chat thread ID
            user_email: Email of user who sent message
            user_name: Display name of user

        Returns:
            Command object if valid command found, None otherwise
        """
        try:
            # Normalize message text
            normalized = message_text.lower().strip()

            # Check if message mentions bot
            if not self._extract_mention(normalized):
                # Not a bot command
                return None

            # Detect command type
            command_type = self._get_command_type(normalized)

            if command_type == CommandType.UNKNOWN:
                # Bot was mentioned but command not recognized
                return Command(
                    command_type=CommandType.UNKNOWN,
                    message_id=message_id,
                    chat_id=chat_id,
                    user_email=user_email,
                    user_name=user_name,
                    parameters={},
                    raw_message=message_text,
                    is_valid=False,
                    error_message="Command not recognized. Try: 'email me', 'email all', 'no emails', or 'summarize again [instructions]'"
                )

            # Extract parameters
            parameters = self._extract_parameters(normalized, command_type)

            # Validate command
            is_valid, error_message = self._validate_command(
                command_type,
                parameters,
                user_email
            )

            command = Command(
                command_type=command_type,
                message_id=message_id,
                chat_id=chat_id,
                user_email=user_email,
                user_name=user_name,
                parameters=parameters,
                raw_message=message_text,
                is_valid=is_valid,
                error_message=error_message
            )

            logger.info(
                f"Parsed command: {command_type.value} from {user_email} in chat {chat_id}"
            )

            return command

        except Exception as e:
            logger.error(f"Error parsing command: {e}", exc_info=True)
            return None

    def parse_reaction(
        self,
        reaction_type: str,
        message_id: str,
        chat_id: str,
        user_email: str,
        user_name: str
    ) -> Optional[Command]:
        """
        Parse emoji reaction as command.

        Args:
            reaction_type: Emoji reaction (e.g., "📧")
            message_id: ID of message that was reacted to
            chat_id: Teams chat thread ID
            user_email: Email of user who reacted
            user_name: Display name of user

        Returns:
            Command object if valid reaction, None otherwise
        """
        try:
            # Only support 📧 email emoji
            if reaction_type not in ["📧", ":email:", "email"]:
                return None

            # Treat as "email me" command
            command = Command(
                command_type=CommandType.EMAIL_ME,
                message_id=message_id,
                chat_id=chat_id,
                user_email=user_email,
                user_name=user_name,
                parameters={"triggered_by": "reaction"},
                raw_message=f"[Reaction: {reaction_type}]",
                is_valid=True
            )

            logger.info(
                f"Parsed reaction: {reaction_type} from {user_email} in chat {chat_id}"
            )

            return command

        except Exception as e:
            logger.error(f"Error parsing reaction: {e}", exc_info=True)
            return None

    def _extract_mention(self, message: str) -> bool:
        """
        Check if message mentions the bot.

        Args:
            message: Normalized message text (lowercase)

        Returns:
            True if bot is mentioned
        """
        for pattern in self.BOT_MENTION_PATTERNS:
            if re.search(pattern, message, re.IGNORECASE):
                return True
        return False

    def _get_command_type(self, message: str) -> CommandType:
        """
        Detect command type from message.

        Args:
            message: Normalized message text (lowercase)

        Returns:
            CommandType enum
        """
        # Check each command type's patterns
        for command_type, patterns in self.COMMAND_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message, re.IGNORECASE):
                    return command_type

        # No matching command
        return CommandType.UNKNOWN

    def _extract_parameters(
        self,
        message: str,
        command_type: CommandType
    ) -> Dict[str, Any]:
        """
        Extract command-specific parameters.

        Args:
            message: Normalized message text (lowercase)
            command_type: Detected command type

        Returns:
            Dictionary of parameters
        """
        parameters = {}

        # Extract custom instructions for summarize_again
        if command_type == CommandType.SUMMARIZE_AGAIN:
            # Pattern: "summarize again <instructions>"
            patterns = [
                r"summarize\s+again\s+(.+)",
                r"re-?summarize\s+(.+)",
                r"regenerate\s+(.+)",
                r"create\s+new\s+summary\s+(.+)",
            ]

            for pattern in patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if match:
                    instructions = match.group(1).strip()
                    parameters["instructions"] = instructions
                    break

            # If no instructions found, use default
            if "instructions" not in parameters:
                parameters["instructions"] = None

        return parameters

    def _validate_command(
        self,
        command_type: CommandType,
        parameters: Dict[str, Any],
        user_email: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate command and parameters.

        Args:
            command_type: Command type
            parameters: Command parameters
            user_email: Email of user who sent command

        Returns:
            Tuple of (is_valid, error_message)
        """
        # All commands are valid at parsing stage
        # Authorization checks (e.g., organizer-only for email_all) happen at processing stage

        if command_type == CommandType.SUMMARIZE_AGAIN:
            # Check if instructions are provided
            if not parameters.get("instructions"):
                return (
                    False,
                    "Please provide instructions for re-summarization. "
                    "Example: 'summarize again focus on engineering tasks'"
                )

        return (True, None)

    def get_help_message(self) -> str:
        """
        Get help message with available commands.

        Returns:
            Formatted help text
        """
        help_text = """
**Meeting Notetaker Bot - Commands**

**Get Personalized Summary:**
- `@meeting notetaker email me` - Get email with your mentions and action items
- Or react with 📧 emoji to any summary message

**Manage Email Preferences:**
- `@meeting notetaker no emails` - Opt out of THIS meeting's emails
- `@meeting notetaker no emails all meetings` - Opt out of ALL meeting emails
- `@meeting notetaker enable emails` - Re-enable meeting emails globally

**Organizer Commands:**
- `@meeting notetaker email all` - Send summary to all participants
- `@meeting notetaker disable distribution` - Disable emails for entire meeting
- `@meeting notetaker enable distribution` - Re-enable emails for meeting

**Re-Summarize Meeting:**
- `@meeting notetaker summarize again [instructions]`
- Example: `@meeting notetaker summarize again focus on engineering decisions`

**Questions?**
Contact your admin or see documentation.
"""
        return help_text.strip()

    def format_error_message(self, command: Command) -> str:
        """
        Format user-friendly error message.

        Args:
            command: Invalid command

        Returns:
            Formatted error message for chat
        """
        if command.error_message:
            return f"❌ {command.error_message}\n\nType `@meeting notetaker help` for available commands."
        else:
            return "❌ Command not recognized. Type `@meeting notetaker help` for available commands."
