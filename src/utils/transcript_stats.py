"""
Transcript Statistics Extractor

Extracts actual meeting statistics from VTT transcript files:
- Actual meeting duration (first to last timestamp)
- Speaker list with speaking times
- Word count per speaker
- Total attendee count (speakers only, unless call records available)
"""

import logging
import re
from typing import Dict, List, Any, Optional
from datetime import timedelta

logger = logging.getLogger(__name__)


class TranscriptStatsExtractor:
    """Extract statistics from VTT transcript content."""

    def __init__(self, vtt_content: str):
        """
        Initialize stats extractor with VTT content.

        Args:
            vtt_content: Raw VTT transcript content
        """
        self.vtt_content = vtt_content
        self.segments = self._parse_segments()

    def _parse_segments(self) -> List[Dict[str, Any]]:
        """
        Parse VTT content into segments with timestamps and speakers.

        Returns:
            List of segment dictionaries
        """
        segments = []

        # Pattern: timestamp line followed by speaker content
        # 00:00:12.345 --> 00:00:15.678
        # <v Speaker Name>Text content</v>
        timestamp_pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})'
        speaker_pattern = r'<v\s+([^>]+)>(.+?)</v>'

        lines = self.vtt_content.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Look for timestamp line
            timestamp_match = re.match(timestamp_pattern, line)
            if timestamp_match:
                start_time = timestamp_match.group(1)
                end_time = timestamp_match.group(2)

                # Next line should have speaker content
                if i + 1 < len(lines):
                    content_line = lines[i + 1]
                    speaker_match = re.search(speaker_pattern, content_line)

                    if speaker_match:
                        speaker = speaker_match.group(1).strip()
                        text = speaker_match.group(2).strip()

                        segments.append({
                            'start': start_time,
                            'end': end_time,
                            'speaker': speaker,
                            'text': text,
                            'word_count': len(text.split())
                        })

                i += 2  # Skip to next segment
            else:
                i += 1

        return segments

    def _timestamp_to_seconds(self, timestamp: str) -> float:
        """
        Convert VTT timestamp to seconds.

        Args:
            timestamp: VTT timestamp (HH:MM:SS.mmm)

        Returns:
            Total seconds as float
        """
        try:
            parts = timestamp.split(':')
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])

            return hours * 3600 + minutes * 60 + seconds
        except:
            return 0.0

    def get_actual_duration(self) -> int:
        """
        Get actual meeting duration in minutes from first to last timestamp.

        Returns:
            Duration in minutes (rounded)
        """
        if not self.segments:
            return 0

        first_timestamp = self.segments[0]['start']
        last_timestamp = self.segments[-1]['end']

        first_seconds = self._timestamp_to_seconds(first_timestamp)
        last_seconds = self._timestamp_to_seconds(last_timestamp)

        duration_seconds = last_seconds - first_seconds
        duration_minutes = round(duration_seconds / 60)

        return max(duration_minutes, 1)  # At least 1 minute

    def get_speaker_stats(self) -> List[Dict[str, Any]]:
        """
        Get statistics for each speaker.

        Returns:
            List of speaker dictionaries with:
            - name: Speaker name
            - segments: Number of times they spoke
            - words: Total words spoken
            - duration_seconds: Total speaking time
            - duration_minutes: Speaking time in minutes (rounded)
            - percentage: Percentage of total speaking time
        """
        speaker_data = {}

        for segment in self.segments:
            speaker = segment['speaker']

            if speaker not in speaker_data:
                speaker_data[speaker] = {
                    'name': speaker,
                    'segments': 0,
                    'words': 0,
                    'duration_seconds': 0.0
                }

            # Calculate segment duration
            start_seconds = self._timestamp_to_seconds(segment['start'])
            end_seconds = self._timestamp_to_seconds(segment['end'])
            segment_duration = end_seconds - start_seconds

            speaker_data[speaker]['segments'] += 1
            speaker_data[speaker]['words'] += segment['word_count']
            speaker_data[speaker]['duration_seconds'] += segment_duration

        # Calculate percentages and format durations
        total_duration = sum(s['duration_seconds'] for s in speaker_data.values())

        speaker_list = []
        for speaker_name, data in speaker_data.items():
            duration_minutes = round(data['duration_seconds'] / 60, 1)
            percentage = (data['duration_seconds'] / total_duration * 100) if total_duration > 0 else 0

            speaker_list.append({
                'name': speaker_name,
                'segments': data['segments'],
                'words': data['words'],
                'duration_seconds': round(data['duration_seconds'], 1),
                'duration_minutes': duration_minutes,
                'percentage': round(percentage, 1)
            })

        # Sort by speaking time (descending)
        speaker_list.sort(key=lambda x: x['duration_seconds'], reverse=True)

        return speaker_list

    def get_summary_stats(self) -> Dict[str, Any]:
        """
        Get summary statistics for the entire transcript.

        Returns:
            Dictionary with:
            - actual_duration_minutes: Actual meeting duration
            - speaker_count: Number of unique speakers
            - total_segments: Total number of speaking segments
            - total_words: Total words spoken
            - speakers: List of speaker stats (sorted by speaking time)
        """
        speaker_stats = self.get_speaker_stats()

        return {
            'actual_duration_minutes': self.get_actual_duration(),
            'speaker_count': len(speaker_stats),
            'total_segments': len(self.segments),
            'total_words': sum(s['words'] for s in speaker_stats),
            'speakers': speaker_stats
        }


def extract_transcript_stats(vtt_content: str) -> Dict[str, Any]:
    """
    Convenience function to extract all transcript statistics.

    Args:
        vtt_content: Raw VTT transcript content

    Returns:
        Dictionary with summary statistics
    """
    try:
        extractor = TranscriptStatsExtractor(vtt_content)
        return extractor.get_summary_stats()
    except Exception as e:
        logger.error(f"Error extracting transcript stats: {e}", exc_info=True)
        return {
            'actual_duration_minutes': 0,
            'speaker_count': 0,
            'total_segments': 0,
            'total_words': 0,
            'speakers': []
        }
