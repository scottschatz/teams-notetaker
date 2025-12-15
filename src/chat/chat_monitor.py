"""
Chat Monitoring Service

Monitors Teams meeting chats for bot commands and reactions.
Polls chats at regular intervals to detect new messages.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ..graph.client import GraphAPIClient
from ..core.database import DatabaseManager, ProcessedChatMessage
from ..chat.command_parser import ChatCommandParser, Command, CommandType
from ..core.exceptions import GraphAPIError


logger = logging.getLogger(__name__)


class ChatMonitor:
    """
    Monitors Teams chats for bot commands.

    Polls meeting chats at regular intervals, detects bot mentions and reactions,
    parses commands, and tracks processed messages to avoid duplicates.

    Usage:
        monitor = ChatMonitor(graph_client, db, parser)

        # Check for new commands in a chat
        commands = monitor.check_for_commands(
            chat_id="19:meeting_abc@thread.v2",
            since=datetime.now() - timedelta(hours=1)
        )

        for command in commands:
            # Process each command
            ...
    """

    def __init__(
        self,
        graph_client: GraphAPIClient,
        db: DatabaseManager,
        parser: ChatCommandParser
    ):
        """
        Initialize chat monitor.

        Args:
            graph_client: GraphAPIClient instance
            db: DatabaseManager instance
            parser: ChatCommandParser instance
        """
        self.graph_client = graph_client
        self.db = db
        self.parser = parser

    def check_for_commands(
        self,
        chat_id: str,
        since: Optional[datetime] = None,
        limit: int = 50
    ) -> List[Command]:
        """
        Check chat for new bot commands.

        Args:
            chat_id: Teams chat thread ID
            since: Only check messages after this timestamp (default: last 24 hours)
            limit: Maximum messages to retrieve (default: 50)

        Returns:
            List of Command objects

        Filters out:
        - Already processed messages
        - Messages not mentioning bot
        - Invalid commands
        """
        try:
            # Default to last 24 hours if not specified
            if not since:
                since = datetime.now() - timedelta(hours=24)

            logger.debug(f"Checking chat {chat_id} for commands since {since}")

            # Get recent messages from chat
            messages = self._get_recent_messages(chat_id, since, limit)

            logger.debug(f"Retrieved {len(messages)} messages from chat {chat_id}")

            commands = []

            for message in messages:
                message_id = message.get("id")
                message_text = message.get("body", {}).get("content", "")
                created_at = message.get("createdDateTime", "")

                # Get sender info (sender can be None for system messages)
                sender = message.get("from")
                if not sender:
                    logger.debug(f"Skipping message {message_id} with no sender (system message)")
                    continue

                user_identity = sender.get("user", {})
                user_email = user_identity.get("userPrincipalName", "")
                user_name = user_identity.get("displayName", "Unknown")

                # Skip if already processed
                if self._is_message_processed(message_id):
                    logger.debug(f"Skipping already processed message {message_id}")
                    continue

                # Skip bot's own messages
                if self._is_bot_message(sender):
                    logger.debug(f"Skipping bot's own message {message_id}")
                    self._mark_message_processed(message_id, chat_id, "bot_message")
                    continue

                # Parse message for command
                command = self.parser.parse_command(
                    message_text=message_text,
                    message_id=message_id,
                    chat_id=chat_id,
                    user_email=user_email,
                    user_name=user_name
                )

                if command:
                    if command.is_valid:
                        # Valid command found
                        commands.append(command)
                        logger.info(
                            f"Found valid command: {command.command_type.value} "
                            f"from {user_email} in chat {chat_id}"
                        )
                    else:
                        # Invalid command - mark as processed with error
                        self._mark_message_processed(
                            message_id,
                            chat_id,
                            "invalid_command",
                            result=command.error_message
                        )
                        logger.warning(
                            f"Invalid command in message {message_id}: "
                            f"{command.error_message}"
                        )

                    # Don't mark valid commands as processed yet
                    # That happens after successful processing by chat_command processor

            logger.info(
                f"Found {len(commands)} valid commands in chat {chat_id} since {since}"
            )

            return commands

        except Exception as e:
            logger.error(
                f"Error checking chat {chat_id} for commands: {e}",
                exc_info=True
            )
            return []

    def check_for_reactions(
        self,
        chat_id: str,
        message_id: str
    ) -> List[Command]:
        """
        Check for emoji reactions on a message.

        Args:
            chat_id: Teams chat thread ID
            message_id: Message ID to check reactions on

        Returns:
            List of Command objects from reactions

        Note: Graph API support for reactions is limited.
        This is a placeholder for future enhancement.
        """
        try:
            logger.debug(f"Checking reactions on message {message_id} in chat {chat_id}")

            # TODO: Implement when Graph API supports reaction queries
            # Currently, Graph API does not provide a straightforward way
            # to query reactions on chat messages

            # Placeholder for future implementation:
            # endpoint = f"/chats/{chat_id}/messages/{message_id}/reactions"
            # reactions = self.graph_client.get(endpoint)

            logger.warning(
                "Reaction checking not yet implemented (Graph API limitation)"
            )

            return []

        except Exception as e:
            logger.error(f"Error checking reactions: {e}", exc_info=True)
            return []

    def _get_recent_messages(
        self,
        chat_id: str,
        since: datetime,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages from chat.

        Args:
            chat_id: Teams chat thread ID
            since: Only get messages after this timestamp
            limit: Maximum messages to retrieve

        Returns:
            List of message objects
        """
        try:
            # Build query parameters
            params = {
                "$top": limit,
                "$orderby": "createdDateTime desc"
            }

            # Get messages from chat
            endpoint = f"/chats/{chat_id}/messages"

            result = self.graph_client.get(endpoint, params=params)

            messages = result.get("value", [])

            # Filter by timestamp
            filtered_messages = []
            for message in messages:
                created_str = message.get("createdDateTime", "")
                if created_str:
                    try:
                        created_dt = datetime.fromisoformat(
                            created_str.replace("Z", "+00:00")
                        )
                        # Convert to naive datetime for comparison
                        created_dt = created_dt.replace(tzinfo=None)
                        if created_dt > since:
                            filtered_messages.append(message)
                    except Exception as e:
                        logger.warning(
                            f"Error parsing message timestamp {created_str}: {e}"
                        )
                        # Include message if timestamp can't be parsed
                        filtered_messages.append(message)

            logger.debug(
                f"Filtered {len(messages)} messages to {len(filtered_messages)} "
                f"after {since}"
            )

            return filtered_messages

        except GraphAPIError as e:
            logger.error(f"Graph API error getting messages for chat {chat_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error getting recent messages: {e}", exc_info=True)
            return []

    def _is_message_processed(self, message_id: str) -> bool:
        """
        Check if message has already been processed.

        Args:
            message_id: Message ID

        Returns:
            True if already processed
        """
        try:
            with self.db.get_session() as session:
                existing = session.query(ProcessedChatMessage).filter_by(
                    message_id=message_id
                ).first()
                return existing is not None

        except Exception as e:
            logger.error(f"Error checking if message processed: {e}")
            # On error, assume not processed (fail-safe)
            return False

    def _mark_message_processed(
        self,
        message_id: str,
        chat_id: str,
        command_type: str,
        result: Optional[str] = None
    ) -> bool:
        """
        Mark message as processed.

        Args:
            message_id: Message ID
            chat_id: Chat thread ID
            command_type: Command type or reason for processing
            result: Result or error message

        Returns:
            True if successfully marked
        """
        try:
            with self.db.get_session() as session:
                record = ProcessedChatMessage(
                    message_id=message_id,
                    chat_id=chat_id,
                    command_type=command_type,
                    result=result
                )
                session.add(record)
                session.commit()

                logger.debug(f"Marked message {message_id} as processed: {command_type}")

                return True

        except Exception as e:
            logger.error(f"Error marking message as processed: {e}", exc_info=True)
            return False

    def _is_bot_message(self, sender: Dict[str, Any]) -> bool:
        """
        Check if message is from the bot itself.

        Args:
            sender: Message sender object

        Returns:
            True if message is from bot

        Note: Assumes bot uses application identity (no user principal name)
        or has specific application ID.
        """
        try:
            # Bot messages typically have applicationIdentity instead of user
            if "application" in sender:
                return True

            # Or check if user is the service account
            user = sender.get("user", {})
            user_type = user.get("userType", "")
            if user_type == "Application":
                return True

            return False

        except Exception as e:
            logger.debug(f"Error checking if bot message: {e}")
            return False

    def mark_command_processed(
        self,
        command: Command,
        success: bool,
        result: Optional[str] = None
    ) -> bool:
        """
        Mark command as processed after execution.

        Args:
            command: Command that was processed
            success: Whether command executed successfully
            result: Result or error message

        Returns:
            True if successfully marked
        """
        try:
            status = "success" if success else "failed"
            command_type = f"{command.command_type.value}:{status}"

            return self._mark_message_processed(
                message_id=command.message_id,
                chat_id=command.chat_id,
                command_type=command_type,
                result=result
            )

        except Exception as e:
            logger.error(f"Error marking command as processed: {e}", exc_info=True)
            return False

    def get_monitoring_stats(self) -> Dict[str, Any]:
        """
        Get statistics about chat monitoring.

        Returns:
            Dictionary with stats:
            - total_processed: Total messages processed
            - commands_by_type: Count by command type
            - recent_activity: Messages processed in last hour
        """
        try:
            with self.db.get_session() as session:
                total = session.query(ProcessedChatMessage).count()

                # Recent activity (last hour)
                recent_cutoff = datetime.now() - timedelta(hours=1)
                recent = session.query(ProcessedChatMessage).filter(
                    ProcessedChatMessage.processed_at > recent_cutoff
                ).count()

                # Commands by type
                from sqlalchemy import func
                command_counts = session.query(
                    ProcessedChatMessage.command_type,
                    func.count(ProcessedChatMessage.message_id)
                ).group_by(
                    ProcessedChatMessage.command_type
                ).all()

                commands_by_type = {
                    cmd_type: count for cmd_type, count in command_counts
                }

                return {
                    "total_processed": total,
                    "recent_activity": recent,
                    "commands_by_type": commands_by_type
                }

        except Exception as e:
            logger.error(f"Error getting monitoring stats: {e}")
            return {
                "total_processed": 0,
                "recent_activity": 0,
                "commands_by_type": {}
            }
