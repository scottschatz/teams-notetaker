"""
Inbox Monitoring Module

Monitors a shared mailbox for user commands:
- Subscribe/resubscribe to meeting summaries
- Unsubscribe from meeting summaries
- Feedback/feature requests

Replaces the broken chat monitoring functionality.
"""

from .email_parser import EmailCommandParser, EmailCommandType
from .inbox_reader import InboxReader
from .inbox_monitor import InboxMonitor

__all__ = [
    "EmailCommandParser",
    "EmailCommandType",
    "InboxReader",
    "InboxMonitor",
]
