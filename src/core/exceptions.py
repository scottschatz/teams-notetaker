"""
Custom exceptions for Teams Meeting Transcript Summarizer.
"""


class TeamsNotetakerException(Exception):
    """Base exception for all custom exceptions."""

    pass


# ============================================================================
# Configuration Exceptions
# ============================================================================


class ConfigurationError(TeamsNotetakerException):
    """Configuration is invalid or missing."""

    pass


# ============================================================================
# Graph API Exceptions
# ============================================================================


class GraphAPIError(TeamsNotetakerException):
    """Error communicating with Microsoft Graph API."""

    pass


class GraphAPIAuthenticationError(GraphAPIError):
    """Authentication failed for Graph API."""

    pass


class GraphAPIRateLimitError(GraphAPIError):
    """Graph API rate limit exceeded."""

    pass


class MeetingNotFoundError(GraphAPIError):
    """Meeting not found in Graph API."""

    pass


class TranscriptNotFoundError(GraphAPIError):
    """Transcript not found for meeting."""

    pass


# ============================================================================
# AI/Claude API Exceptions
# ============================================================================


class ClaudeAPIError(TeamsNotetakerException):
    """Error communicating with Claude API."""

    pass


class ClaudeAPIRateLimitError(ClaudeAPIError):
    """Claude API rate limit exceeded."""

    pass


class SummaryGenerationError(ClaudeAPIError):
    """Failed to generate summary."""

    pass


# ============================================================================
# Database Exceptions
# ============================================================================


class DatabaseError(TeamsNotetakerException):
    """Database operation failed."""

    pass


class JobNotFoundError(DatabaseError):
    """Job not found in database."""

    pass


class MeetingAlreadyExistsError(DatabaseError):
    """Meeting already exists in database."""

    pass


# ============================================================================
# Job Processing Exceptions
# ============================================================================


class JobProcessingError(TeamsNotetakerException):
    """Job processing failed."""

    pass


class JobTimeoutError(JobProcessingError):
    """Job exceeded timeout limit."""

    pass


class JobDependencyError(JobProcessingError):
    """Job dependency not satisfied."""

    pass


# ============================================================================
# Distribution Exceptions
# ============================================================================


class DistributionError(TeamsNotetakerException):
    """Distribution failed."""

    pass


class EmailSendError(DistributionError):
    """Failed to send email."""

    pass


class TeamsChatPostError(DistributionError):
    """Failed to post to Teams chat."""

    pass


# ============================================================================
# Authentication Exceptions
# ============================================================================


class AuthenticationError(TeamsNotetakerException):
    """Authentication failed."""

    pass


class SessionExpiredError(AuthenticationError):
    """User session expired."""

    pass


class UnauthorizedError(AuthenticationError):
    """User not authorized for this action."""

    pass


# ============================================================================
# Parsing Exceptions
# ============================================================================


class VTTParseError(TeamsNotetakerException):
    """Failed to parse VTT transcript."""

    pass
