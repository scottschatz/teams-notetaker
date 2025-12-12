"""
Meeting Summarizer

Generates AI-powered summaries of meeting transcripts using Claude API.
Uses prompt templates from prompts.py and handles token limits.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from ..core.config import ClaudeConfig
from ..ai.claude_client import ClaudeClient
from ..ai.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_summary_prompt,
    build_action_items_extraction_prompt,
    build_decision_extraction_prompt,
    estimate_token_count
)
from ..core.exceptions import SummaryGenerationError


logger = logging.getLogger(__name__)


class MeetingSummarizer:
    """
    Generates AI summaries of meeting transcripts.

    Features:
    - Multiple summary types (full, action items only, decisions only)
    - Token limit enforcement with smart truncation
    - Speaker attribution preservation
    - Metadata inclusion (meeting details)
    - Cost tracking

    Usage:
        config = ClaudeConfig(api_key='...', model='claude-sonnet-4-20250514')
        summarizer = MeetingSummarizer(config)

        result = summarizer.summarize_meeting(
            transcript="...",
            meeting_metadata={...},
            summary_type="full"
        )

        print(result['summary'])
        print(f"Cost: ${result['cost']:.4f}")
    """

    def __init__(self, config: ClaudeConfig):
        """
        Initialize meeting summarizer.

        Args:
            config: ClaudeConfig with API key and model settings
        """
        self.config = config
        self.client = ClaudeClient(config)

        logger.info(f"MeetingSummarizer initialized (model: {config.model})")

    def summarize_meeting(
        self,
        transcript: str,
        meeting_metadata: Dict[str, Any],
        summary_type: str = "full",
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate meeting summary from transcript.

        Args:
            transcript: Meeting transcript text (formatted with speakers)
            meeting_metadata: Dictionary with:
                - subject: Meeting title
                - organizer_name: Organizer name
                - start_time: Start datetime (ISO format)
                - end_time: End datetime (ISO format)
                - duration_minutes: Duration in minutes
                - participant_count: Number of participants
                - participants: List of participant names (optional)
            summary_type: Type of summary ("full", "action_items", "decisions", "executive")
            max_tokens: Maximum tokens for summary (default from config)

        Returns:
            Dictionary with:
                - summary: Generated summary text (markdown)
                - input_tokens: Input token count
                - output_tokens: Output token count
                - total_tokens: Total tokens used
                - cost: Estimated cost in USD
                - model: Model used
                - summary_type: Type of summary generated
                - generation_time_ms: Time taken in milliseconds
                - truncated: Whether transcript was truncated

        Raises:
            SummaryGenerationError: If summary generation fails
        """
        try:
            if max_tokens is None:
                max_tokens = self.config.max_tokens

            logger.info(
                f"Generating {summary_type} summary for meeting '{meeting_metadata.get('subject', 'Unknown')}' "
                f"(transcript: {len(transcript)} chars, max_tokens: {max_tokens})"
            )

            # Build prompt based on summary type
            if summary_type == "full":
                user_prompt = build_summary_prompt(transcript, meeting_metadata)
            elif summary_type == "action_items":
                user_prompt = build_action_items_extraction_prompt(transcript, meeting_metadata)
            elif summary_type == "decisions":
                user_prompt = build_decision_extraction_prompt(transcript, meeting_metadata)
            elif summary_type == "executive":
                # For executive summary, use a condensed version
                user_prompt = self._build_executive_prompt(transcript, meeting_metadata)
            else:
                raise SummaryGenerationError(f"Unknown summary type: {summary_type}")

            # Check token count and truncate if needed
            input_token_estimate = estimate_token_count(user_prompt)
            truncated = False

            # Reserve tokens for output (default max_tokens for response)
            max_input_tokens = 180000  # Claude 3.5 Sonnet context: 200k tokens, leave buffer

            if input_token_estimate > max_input_tokens:
                logger.warning(
                    f"Prompt too long ({input_token_estimate} tokens), truncating to {max_input_tokens} tokens"
                )
                user_prompt = truncate_to_token_limit(user_prompt, max_input_tokens)
                truncated = True

            # Generate summary
            response = self.client.generate_text(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=1.0  # Default temperature for balanced creativity
            )

            # Add metadata to response
            response["summary"] = response.pop("content")
            response["summary_type"] = summary_type
            response["truncated"] = truncated

            logger.info(
                f"✓ Generated {summary_type} summary: {response['output_tokens']} tokens, "
                f"${response['cost']:.4f}, {response['generation_time_ms']}ms"
            )

            return response

        except SummaryGenerationError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}", exc_info=True)
            raise SummaryGenerationError(f"Summary generation failed: {e}")

    def summarize_multiple_meetings(
        self,
        meetings: List[Dict[str, Any]],
        summary_type: str = "executive"
    ) -> Dict[str, Any]:
        """
        Generate combined summary for multiple meetings.

        Useful for weekly/monthly digests.

        Args:
            meetings: List of meeting dictionaries, each with:
                - transcript: Transcript text
                - metadata: Meeting metadata dict
            summary_type: Type of summary (default: executive)

        Returns:
            Same format as summarize_meeting()
        """
        try:
            logger.info(f"Generating combined summary for {len(meetings)} meetings")

            # Build combined transcript
            combined_sections = []
            for i, meeting in enumerate(meetings, 1):
                metadata = meeting["metadata"]
                transcript = meeting["transcript"]

                section = f"## Meeting {i}: {metadata.get('subject', 'Unknown')}\n"
                section += f"Date: {metadata.get('start_time', 'Unknown')}\n"
                section += f"Duration: {metadata.get('duration_minutes', 0)} minutes\n\n"
                section += f"Transcript:\n{transcript}\n\n"
                section += "---\n\n"

                combined_sections.append(section)

            combined_transcript = "".join(combined_sections)

            # Generate summary with special prompt for multiple meetings
            user_prompt = f"""Please summarize the following {len(meetings)} meetings.

For each meeting, provide:
1. Brief overview (1-2 sentences)
2. Key decisions or outcomes
3. Notable action items

Then provide:
4. Cross-meeting themes or patterns
5. Overall recommendations

Meetings:

{combined_transcript}
"""

            response = self.client.generate_text(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=self.config.max_tokens,
                temperature=1.0
            )

            response["summary"] = response.pop("content")
            response["summary_type"] = "multi_meeting"
            response["meeting_count"] = len(meetings)

            logger.info(f"✓ Generated combined summary for {len(meetings)} meetings")

            return response

        except Exception as e:
            logger.error(f"Failed to generate multi-meeting summary: {e}", exc_info=True)
            raise SummaryGenerationError(f"Multi-meeting summary failed: {e}")

    def _build_executive_prompt(self, transcript: str, metadata: Dict[str, Any]) -> str:
        """
        Build executive summary prompt (condensed format).

        Args:
            transcript: Meeting transcript
            metadata: Meeting metadata

        Returns:
            Formatted prompt string
        """
        subject = metadata.get("subject", "Meeting")
        organizer = metadata.get("organizer_name", "Unknown")
        start_time = metadata.get("start_time", "Unknown")
        duration = metadata.get("duration_minutes", 0)

        return f"""Generate a brief executive summary of this meeting.

**Meeting Details:**
- Subject: {subject}
- Organizer: {organizer}
- Date: {start_time}
- Duration: {duration} minutes

**Format (keep it concise):**
1. One-sentence overview
2. 2-3 key takeaways (bullet points)
3. Critical action items only (if any)

**Transcript:**

{transcript}

**Instructions:**
- Be extremely concise (max 200 words total)
- Focus on decisions and outcomes, not discussion
- Only include action items if they're clearly stated
- Use bullet points for clarity
"""

    def estimate_summary_cost(
        self,
        transcript: str,
        metadata: Dict[str, Any],
        summary_type: str = "full"
    ) -> float:
        """
        Estimate cost before generating summary.

        Args:
            transcript: Meeting transcript
            metadata: Meeting metadata
            summary_type: Type of summary

        Returns:
            Estimated cost in USD
        """
        # Build prompt
        if summary_type == "full":
            user_prompt = build_summary_prompt(transcript, metadata)
        elif summary_type == "action_items":
            user_prompt = build_action_items_extraction_prompt(transcript, metadata)
        elif summary_type == "decisions":
            user_prompt = build_decision_extraction_prompt(transcript, metadata)
        else:
            user_prompt = self._build_executive_prompt(transcript, metadata)

        # Estimate cost
        return self.client.estimate_cost(user_prompt, expected_output_tokens=self.config.max_tokens)
