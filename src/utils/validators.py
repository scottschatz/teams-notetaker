"""
Input validation utilities.
"""

import re
from typing import Optional
from datetime import datetime


def validate_email(email: str) -> bool:
    """
    Validate email address format.

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise

    Example:
        validate_email("user@example.com") -> True
        validate_email("invalid.email") -> False
    """
    if not email:
        return False

    # RFC 5322 compliant regex (simplified)
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

    return bool(re.match(pattern, email))


def validate_domain(email: str, allowed_domain: str) -> bool:
    """
    Validate that email is from allowed domain.

    Args:
        email: Email address to check
        allowed_domain: Allowed domain (e.g., "townsquaremedia.com")

    Returns:
        True if email is from allowed domain, False otherwise

    Example:
        validate_domain("user@townsquaremedia.com", "townsquaremedia.com") -> True
        validate_domain("user@gmail.com", "townsquaremedia.com") -> False
    """
    if not email or not allowed_domain:
        return False

    if "@" not in email:
        return False

    email_domain = email.split("@")[1].lower()
    allowed_domain = allowed_domain.lower()

    return email_domain == allowed_domain


def validate_meeting_id(meeting_id: str) -> bool:
    """
    Validate Microsoft Teams meeting ID format.

    Meeting IDs are typically long alphanumeric strings.

    Args:
        meeting_id: Meeting ID to validate

    Returns:
        True if valid format, False otherwise
    """
    if not meeting_id:
        return False

    # Basic validation: should be alphanumeric, underscore, hyphen
    # Typical length: 50-200 characters
    if len(meeting_id) < 10 or len(meeting_id) > 500:
        return False

    # Should only contain safe characters
    pattern = r"^[a-zA-Z0-9_-]+$"
    return bool(re.match(pattern, meeting_id))


def validate_url(url: str) -> bool:
    """
    Validate URL format.

    Args:
        url: URL to validate

    Returns:
        True if valid, False otherwise
    """
    if not url:
        return False

    # Simple URL validation
    pattern = r"^https?://[^\s/$.?#].[^\s]*$"
    return bool(re.match(pattern, url))


def validate_datetime_string(datetime_str: str, format: str = "%Y-%m-%d %H:%M:%S") -> bool:
    """
    Validate datetime string format.

    Args:
        datetime_str: Datetime string to validate
        format: Expected format (default: "%Y-%m-%d %H:%M:%S")

    Returns:
        True if valid, False otherwise

    Example:
        validate_datetime_string("2025-12-10 14:30:00") -> True
        validate_datetime_string("invalid") -> False
    """
    if not datetime_str:
        return False

    try:
        datetime.strptime(datetime_str, format)
        return True
    except ValueError:
        return False


def validate_config_value(value: str, data_type: str) -> tuple[bool, Optional[str]]:
    """
    Validate configuration value based on data type.

    Args:
        value: Value to validate
        data_type: Expected type ('string', 'int', 'bool', 'json')

    Returns:
        (is_valid, error_message)

    Example:
        validate_config_value("123", "int") -> (True, None)
        validate_config_value("abc", "int") -> (False, "Invalid integer")
    """
    if data_type == "string":
        return True, None

    elif data_type == "int":
        try:
            int(value)
            return True, None
        except ValueError:
            return False, f"Invalid integer: {value}"

    elif data_type == "bool":
        if value.lower() in ("true", "false", "1", "0", "yes", "no"):
            return True, None
        return False, f"Invalid boolean: {value} (use true/false)"

    elif data_type == "json":
        try:
            import json

            json.loads(value)
            return True, None
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

    else:
        return False, f"Unknown data type: {data_type}"


def sanitize_input(text: str, max_length: Optional[int] = None, allow_html: bool = False) -> str:
    """
    Sanitize user input to prevent XSS and other attacks.

    Args:
        text: Input text to sanitize
        max_length: Maximum length (truncate if longer)
        allow_html: Whether to allow HTML tags

    Returns:
        Sanitized text
    """
    if not text:
        return ""

    # Remove control characters
    text = "".join(char for char in text if ord(char) >= 32 or char in "\n\r\t")

    # Remove HTML if not allowed
    if not allow_html:
        text = re.sub(r"<[^>]+>", "", text)

    # Truncate if needed
    if max_length and len(text) > max_length:
        text = text[:max_length]

    return text.strip()


def validate_token_count(text: str, max_tokens: int = 100000) -> tuple[bool, int]:
    """
    Validate that text doesn't exceed token limit.

    Uses rough estimate: ~4 characters per token.

    Args:
        text: Text to validate
        max_tokens: Maximum tokens allowed

    Returns:
        (is_valid, estimated_tokens)
    """
    estimated_tokens = len(text) // 4

    return estimated_tokens <= max_tokens, estimated_tokens


def validate_positive_int(value: int, min_value: int = 1) -> tuple[bool, Optional[str]]:
    """
    Validate that value is a positive integer.

    Args:
        value: Value to validate
        min_value: Minimum allowed value (default: 1)

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(value, int):
        return False, f"Expected integer, got {type(value).__name__}"

    if value < min_value:
        return False, f"Value must be >= {min_value}, got {value}"

    return True, None


def validate_duration(duration_minutes: int) -> tuple[bool, Optional[str]]:
    """
    Validate meeting duration.

    Args:
        duration_minutes: Duration in minutes

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(duration_minutes, int):
        return False, "Duration must be an integer"

    if duration_minutes < 1:
        return False, "Duration must be at least 1 minute"

    if duration_minutes > 1440:  # 24 hours
        return False, "Duration cannot exceed 24 hours (1440 minutes)"

    return True, None
