"""
Text processing utilities for Teams Meeting Transcript Summarizer.
"""

import re
from typing import List, Optional
from datetime import datetime


def clean_text(text: str) -> str:
    """
    Clean and normalize text.

    - Remove extra whitespace
    - Normalize line endings
    - Trim leading/trailing whitespace

    Args:
        text: Text to clean

    Returns:
        Cleaned text
    """
    if not text:
        return ""

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove extra whitespace
    text = " ".join(text.split())

    # Trim
    text = text.strip()

    return text


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum length, adding suffix if truncated.

    Args:
        text: Text to truncate
        max_length: Maximum length (including suffix)
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated text

    Example:
        truncate_text("This is a long sentence", 10)
        -> "This is..."
    """
    if len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def extract_emails(text: str) -> List[str]:
    """
    Extract email addresses from text.

    Args:
        text: Text containing email addresses

    Returns:
        List of email addresses found

    Example:
        extract_emails("Contact john@example.com or sarah@example.com")
        -> ["john@example.com", "sarah@example.com"]
    """
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    emails = re.findall(email_pattern, text)
    return list(set(emails))  # Remove duplicates


def mask_email(email: str) -> str:
    """
    Mask email address for display.

    Args:
        email: Email address to mask

    Returns:
        Masked email

    Example:
        mask_email("john.smith@example.com")
        -> "j***h@example.com"
    """
    if "@" not in email:
        return email

    local, domain = email.split("@", 1)

    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]

    return f"{masked_local}@{domain}"


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize string for use as filename.

    Removes/replaces invalid filename characters.

    Args:
        filename: Original filename
        max_length: Maximum filename length (default: 255)

    Returns:
        Safe filename

    Example:
        sanitize_filename("Meeting: Q1 2025 <Draft>")
        -> "Meeting_Q1_2025_Draft"
    """
    # Replace invalid characters
    safe = re.sub(r'[<>:"/\\|?*]', "_", filename)

    # Remove control characters
    safe = "".join(char for char in safe if ord(char) >= 32)

    # Collapse multiple underscores
    safe = re.sub(r"_+", "_", safe)

    # Trim
    safe = safe.strip("_. ")

    # Truncate if needed
    if len(safe) > max_length:
        safe = safe[:max_length]

    return safe


def word_count(text: str) -> int:
    """
    Count words in text.

    Args:
        text: Text to count

    Returns:
        Number of words
    """
    if not text:
        return 0

    return len(text.split())


def char_count(text: str, exclude_whitespace: bool = False) -> int:
    """
    Count characters in text.

    Args:
        text: Text to count
        exclude_whitespace: Exclude whitespace from count

    Returns:
        Number of characters
    """
    if not text:
        return 0

    if exclude_whitespace:
        return len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

    return len(text)


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration

    Example:
        format_duration(145.5) -> "2m 25s"
        format_duration(3665) -> "1h 1m 5s"
    """
    if seconds < 0:
        return "0s"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


def format_datetime_relative(dt: datetime) -> str:
    """
    Format datetime relative to now.

    Args:
        dt: Datetime to format

    Returns:
        Relative time string

    Example:
        format_datetime_relative(now - 2 hours) -> "2 hours ago"
        format_datetime_relative(now + 1 day) -> "in 1 day"
    """
    now = datetime.now()

    # Handle timezone-naive datetimes
    if dt.tzinfo is None and now.tzinfo is not None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo is not None and now.tzinfo is None:
        from datetime import timezone

        now = now.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = diff.total_seconds()

    # Future
    if seconds < 0:
        seconds = abs(seconds)
        suffix = "from now"
    else:
        suffix = "ago"

    # Format
    if seconds < 60:
        return f"{int(seconds)} seconds {suffix}"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} {suffix}"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} {suffix}"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} {suffix}"
    else:
        # Just show the date
        return dt.strftime("%Y-%m-%d %H:%M")


def extract_action_items(text: str) -> List[str]:
    """
    Extract potential action items from text.

    Looks for patterns like:
    - "TODO: ..."
    - "Action: ..."
    - "[ ] ..." (markdown checkbox)
    - Lines starting with action verbs

    Args:
        text: Text to search

    Returns:
        List of potential action items
    """
    action_items = []

    # Pattern 1: TODO/Action/FIXME prefixes
    patterns = [
        r"TODO:\s*(.+)",
        r"Action:\s*(.+)",
        r"FIXME:\s*(.+)",
        r"TASK:\s*(.+)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        action_items.extend(matches)

    # Pattern 2: Markdown checkboxes
    checkbox_matches = re.findall(r"- \[ \]\s*(.+)", text, re.MULTILINE)
    action_items.extend(checkbox_matches)

    # Clean and deduplicate
    action_items = [clean_text(item) for item in action_items]
    action_items = list(set(action_items))  # Remove duplicates

    return action_items


def highlight_keywords(text: str, keywords: List[str], tag: str = "**") -> str:
    """
    Highlight keywords in text.

    Args:
        text: Text to process
        keywords: Keywords to highlight
        tag: Tag to wrap keywords with (default: "**" for markdown bold)

    Returns:
        Text with highlighted keywords

    Example:
        highlight_keywords("Find the action items", ["action"], "**")
        -> "Find the **action** items"
    """
    for keyword in keywords:
        # Case-insensitive replacement
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        text = pattern.sub(f"{tag}{keyword}{tag}", text)

    return text


def split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences.

    Simple sentence splitting - works for most English text.

    Args:
        text: Text to split

    Returns:
        List of sentences
    """
    # Simple split on period, exclamation, question mark
    # followed by space and capital letter
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

    # Clean each sentence
    sentences = [s.strip() for s in sentences if s.strip()]

    return sentences


def create_excerpt(text: str, max_words: int = 50) -> str:
    """
    Create excerpt from text.

    Args:
        text: Full text
        max_words: Maximum words in excerpt

    Returns:
        Excerpt with ellipsis if truncated

    Example:
        create_excerpt("This is a very long meeting summary...", 5)
        -> "This is a very long..."
    """
    words = text.split()

    if len(words) <= max_words:
        return text

    excerpt_words = words[:max_words]
    return " ".join(excerpt_words) + "..."
