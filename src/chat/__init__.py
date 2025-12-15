"""
Chat Integration Module

Provides Teams chat command parsing and monitoring for meeting notetaker bot.
"""

from .command_parser import ChatCommandParser, Command, CommandType
from .chat_monitor import ChatMonitor

__all__ = ["ChatCommandParser", "Command", "CommandType", "ChatMonitor"]
