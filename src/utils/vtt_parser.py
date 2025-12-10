"""
VTT (WebVTT) transcript parser for Teams Meeting Transcripts.

Parses VTT format transcripts from Microsoft Teams meetings into
structured data for AI summarization.

VTT Format Example:
    WEBVTT

    00:00:00.000 --> 00:00:05.000
    <v Speaker1>Hello everyone, welcome to the meeting.</v>

    00:00:05.500 --> 00:00:10.000
    <v Speaker2>Thanks for joining us today.</v>
"""

import re
from typing import List, Dict, Optional
from datetime import timedelta
import logging


class VTTParseError(Exception):
    """Error parsing VTT file."""

    pass


def parse_vtt(vtt_content: str) -> List[Dict[str, str]]:
    """
    Parse VTT transcript into structured format.

    Args:
        vtt_content: Raw VTT file content

    Returns:
        List of transcript segments:
        [
            {
                "timestamp": "00:00:00.000",
                "speaker": "Speaker1",
                "text": "Hello everyone, welcome to the meeting.",
                "start_seconds": 0.0,
                "end_seconds": 5.0
            },
            ...
        ]

    Raises:
        VTTParseError: If VTT content is invalid
    """
    logger = logging.getLogger(__name__)

    if not vtt_content or not vtt_content.strip():
        raise VTTParseError("VTT content is empty")

    # Check for WEBVTT header
    if not vtt_content.strip().startswith("WEBVTT"):
        raise VTTParseError("Invalid VTT file: missing WEBVTT header")

    segments = []
    lines = vtt_content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines and header
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            i += 1
            continue

        # Check if this is a timestamp line (contains -->)
        if "-->" in line:
            try:
                # Parse timestamp line
                timestamp_match = re.match(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})", line)

                if timestamp_match:
                    start_time = timestamp_match.group(1)
                    end_time = timestamp_match.group(2)

                    # Convert to seconds
                    start_seconds = _timestamp_to_seconds(start_time)
                    end_seconds = _timestamp_to_seconds(end_time)

                    # Next line should contain the text (possibly with speaker tag)
                    i += 1
                    if i < len(lines):
                        text_line = lines[i].strip()

                        # Extract speaker and text
                        speaker, text = _extract_speaker_and_text(text_line)

                        if text:  # Only add if there's actual text
                            segments.append(
                                {
                                    "timestamp": start_time,
                                    "end_timestamp": end_time,
                                    "speaker": speaker,
                                    "text": text,
                                    "start_seconds": start_seconds,
                                    "end_seconds": end_seconds,
                                }
                            )

            except Exception as e:
                logger.warning(f"Failed to parse segment at line {i}: {e}")

        i += 1

    if not segments:
        raise VTTParseError("No valid segments found in VTT file")

    logger.info(f"Parsed {len(segments)} segments from VTT transcript")
    return segments


def _timestamp_to_seconds(timestamp: str) -> float:
    """
    Convert VTT timestamp to seconds.

    Args:
        timestamp: Timestamp in format "HH:MM:SS.mmm"

    Returns:
        Total seconds as float

    Example:
        "00:01:30.500" -> 90.5
    """
    parts = timestamp.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds_parts = parts[2].split(".")
    seconds = int(seconds_parts[0])
    milliseconds = int(seconds_parts[1]) if len(seconds_parts) > 1 else 0

    total_seconds = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0
    return total_seconds


def _extract_speaker_and_text(text_line: str) -> tuple[str, str]:
    """
    Extract speaker name and text from VTT text line.

    VTT format uses <v SpeakerName>text</v> for speaker attribution.

    Args:
        text_line: Text line from VTT file

    Returns:
        (speaker_name, text) tuple

    Examples:
        "<v John>Hello everyone</v>" -> ("John", "Hello everyone")
        "Hello everyone" -> ("Unknown", "Hello everyone")
    """
    # Try to extract speaker tag: <v SpeakerName>text</v>
    speaker_match = re.match(r"<v\s+([^>]+)>(.+?)</v>", text_line)

    if speaker_match:
        speaker = speaker_match.group(1).strip()
        text = speaker_match.group(2).strip()
        return speaker, text

    # Try alternative format: <v SpeakerName>text (no closing tag)
    speaker_match = re.match(r"<v\s+([^>]+)>(.+)", text_line)

    if speaker_match:
        speaker = speaker_match.group(1).strip()
        text = speaker_match.group(2).strip()
        return speaker, text

    # No speaker tag found, return unknown speaker
    return "Unknown", text_line.strip()


def get_transcript_metadata(segments: List[Dict[str, str]]) -> Dict:
    """
    Extract metadata from parsed transcript segments.

    Args:
        segments: Parsed transcript segments from parse_vtt()

    Returns:
        Metadata dictionary with:
        - total_duration_seconds: Total duration in seconds
        - word_count: Total word count
        - speaker_count: Number of unique speakers
        - speakers: List of unique speaker names
        - segment_count: Number of segments
    """
    if not segments:
        return {
            "total_duration_seconds": 0,
            "word_count": 0,
            "speaker_count": 0,
            "speakers": [],
            "segment_count": 0,
        }

    # Calculate duration
    last_segment = segments[-1]
    total_duration = last_segment["end_seconds"]

    # Count words
    word_count = sum(len(seg["text"].split()) for seg in segments)

    # Get unique speakers
    speakers = list(set(seg["speaker"] for seg in segments))
    speaker_count = len(speakers)

    return {
        "total_duration_seconds": total_duration,
        "word_count": word_count,
        "speaker_count": speaker_count,
        "speakers": sorted(speakers),
        "segment_count": len(segments),
    }


def format_transcript_for_summary(segments: List[Dict[str, str]], include_timestamps: bool = True) -> str:
    """
    Format parsed transcript segments into readable text for AI summarization.

    Args:
        segments: Parsed transcript segments from parse_vtt()
        include_timestamps: Whether to include timestamps in output

    Returns:
        Formatted transcript text

    Example:
        [00:00:00] John: Hello everyone, welcome to the meeting.
        [00:00:05] Sarah: Thanks for joining us today.
    """
    if not segments:
        return ""

    lines = []
    current_speaker = None

    for segment in segments:
        speaker = segment["speaker"]
        text = segment["text"]

        # Group consecutive segments from same speaker
        if speaker != current_speaker:
            current_speaker = speaker
            if include_timestamps:
                timestamp = segment["timestamp"].split(".")[0]  # Remove milliseconds
                lines.append(f"[{timestamp}] {speaker}: {text}")
            else:
                lines.append(f"{speaker}: {text}")
        else:
            # Continue previous speaker's text
            if lines:
                lines[-1] += " " + text

    return "\n".join(lines)


def filter_segments_by_speaker(segments: List[Dict[str, str]], speaker_name: str) -> List[Dict[str, str]]:
    """
    Filter transcript segments to only those from a specific speaker.

    Args:
        segments: Parsed transcript segments
        speaker_name: Name of speaker to filter by

    Returns:
        Filtered list of segments
    """
    return [seg for seg in segments if seg["speaker"].lower() == speaker_name.lower()]


def filter_segments_by_time_range(
    segments: List[Dict[str, str]], start_seconds: float, end_seconds: float
) -> List[Dict[str, str]]:
    """
    Filter transcript segments to only those within a time range.

    Args:
        segments: Parsed transcript segments
        start_seconds: Start time in seconds
        end_seconds: End time in seconds

    Returns:
        Filtered list of segments
    """
    return [seg for seg in segments if seg["start_seconds"] >= start_seconds and seg["end_seconds"] <= end_seconds]


def get_speaker_stats(segments: List[Dict[str, str]]) -> Dict[str, Dict]:
    """
    Get statistics for each speaker in the transcript.

    Args:
        segments: Parsed transcript segments

    Returns:
        Dictionary of speaker statistics:
        {
            "Speaker1": {
                "segment_count": 10,
                "word_count": 150,
                "total_duration_seconds": 45.5,
                "percentage_of_time": 25.3
            },
            ...
        }
    """
    if not segments:
        return {}

    stats = {}
    total_duration = segments[-1]["end_seconds"] if segments else 0

    for segment in segments:
        speaker = segment["speaker"]

        if speaker not in stats:
            stats[speaker] = {
                "segment_count": 0,
                "word_count": 0,
                "total_duration_seconds": 0.0,
                "percentage_of_time": 0.0,
            }

        duration = segment["end_seconds"] - segment["start_seconds"]
        word_count = len(segment["text"].split())

        stats[speaker]["segment_count"] += 1
        stats[speaker]["word_count"] += word_count
        stats[speaker]["total_duration_seconds"] += duration

    # Calculate percentages
    if total_duration > 0:
        for speaker in stats:
            percentage = (stats[speaker]["total_duration_seconds"] / total_duration) * 100
            stats[speaker]["percentage_of_time"] = round(percentage, 1)

    return stats
