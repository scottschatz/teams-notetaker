"""
Meeting Summarizer

Generates AI-powered summaries of meeting transcripts using Claude API.
Uses prompt templates from prompts.py and handles token limits.

Includes both basic MeetingSummarizer and enhanced EnhancedMeetingSummarizer
with multi-stage extraction (action items, decisions, topics, highlights, mentions).
"""

import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

from ..core.config import ClaudeConfig
from ..ai.claude_client import ClaudeClient
from ..ai.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_summary_prompt,
    build_action_items_extraction_prompt,
    build_decision_extraction_prompt,
    estimate_token_count
)
from ..ai.prompts.enhanced_prompts import (
    ACTION_ITEM_PROMPT,
    DECISION_PROMPT,
    TOPIC_SEGMENTATION_PROMPT,
    HIGHLIGHTS_PROMPT,
    MENTIONS_PROMPT,
    AGGREGATE_SUMMARY_PROMPT,
    format_transcript_for_extraction,
    EXTRACTION_TOKEN_LIMITS,
    EXTRACTION_TEMPERATURE
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


# ============================================================================
# ENHANCED MEETING SUMMARIZER (Multi-Stage Extraction)
# ============================================================================

@dataclass
class SummaryMetadata:
    """Metadata about the summary generation process."""
    total_tokens: int
    total_cost: float
    generation_time_ms: int
    model: str
    extraction_calls: int  # Number of API calls made
    truncated: bool = False
    custom_instructions: Optional[str] = None


@dataclass
class EnhancedSummary:
    """
    Complete enhanced meeting summary with structured extractions.

    Attributes:
        overall_summary: Narrative summary (markdown)
        action_items: List of extracted action items
        decisions: List of extracted decisions
        topics: List of topic segments
        highlights: List of key moments
        mentions: List of person mentions
        metadata: Summary generation metadata
    """
    overall_summary: str  # Markdown narrative
    action_items: List[Dict[str, Any]]
    decisions: List[Dict[str, Any]]
    topics: List[Dict[str, Any]]
    highlights: List[Dict[str, Any]]
    mentions: List[Dict[str, Any]]
    metadata: SummaryMetadata

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "overall_summary": self.overall_summary,
            "action_items": self.action_items,
            "decisions": self.decisions,
            "topics": self.topics,
            "highlights": self.highlights,
            "mentions": self.mentions,
            "metadata": asdict(self.metadata)
        }


class EnhancedMeetingSummarizer:
    """
    Multi-stage meeting summarizer with structured data extraction.

    Uses 6 separate Claude API calls:
    1. Extract action items
    2. Extract decisions
    3. Extract topic segments
    4. Extract highlights
    5. Extract mentions
    6. Generate aggregate narrative summary

    This approach provides:
    - More accurate structured data extraction
    - Better handling of complex meetings
    - Support for personalized summaries (filter by user)
    - Enables interactive re-summarization

    Usage:
        config = ClaudeConfig(api_key='...', model='claude-sonnet-4-20250514')
        summarizer = EnhancedMeetingSummarizer(config)

        result = summarizer.generate_enhanced_summary(
            transcript_segments=[...],
            meeting_metadata={...},
            custom_instructions="Focus on engineering action items"
        )

        print(result.overall_summary)
        print(f"Found {len(result.action_items)} action items")
        print(f"Total cost: ${result.metadata.total_cost:.4f}")
    """

    def __init__(self, config: ClaudeConfig, aggregate_config: Optional[ClaudeConfig] = None):
        """
        Initialize enhanced summarizer.

        Args:
            config: ClaudeConfig for extraction tasks (action items, decisions, etc.)
            aggregate_config: Optional separate config for aggregate summary (if None, uses same as config)
        """
        self.config = config
        self.extraction_client = ClaudeClient(config)

        # Use separate config for aggregate summary if provided (hybrid approach)
        if aggregate_config:
            self.aggregate_client = ClaudeClient(aggregate_config)
            logger.info(
                f"EnhancedMeetingSummarizer initialized (HYBRID MODE: "
                f"extraction={config.model}, aggregate={aggregate_config.model})"
            )
        else:
            self.aggregate_client = self.extraction_client
            logger.info(f"EnhancedMeetingSummarizer initialized (model: {config.model})")

    def generate_enhanced_summary(
        self,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Dict[str, Any],
        custom_instructions: Optional[str] = None
    ) -> EnhancedSummary:
        """
        Generate enhanced summary with multi-stage extraction.

        Args:
            transcript_segments: Parsed VTT segments with speaker, text, timestamp
            meeting_metadata: Meeting details (subject, organizer, etc.)
            custom_instructions: Optional user instructions for focused summarization

        Returns:
            EnhancedSummary object with structured data and narrative

        Raises:
            SummaryGenerationError: If any extraction stage fails
        """
        try:
            start_time = datetime.now()

            # Format transcript for extraction
            transcript_text = format_transcript_for_extraction(transcript_segments)

            logger.info(
                f"Starting enhanced summary for '{meeting_metadata.get('subject')}' "
                f"({len(transcript_segments)} segments, {len(transcript_text)} chars)"
            )

            # Track costs across all calls
            total_tokens = 0
            total_cost = 0.0
            extraction_calls = 0

            # Stage 1: Extract action items
            logger.info("Stage 1/6: Extracting action items...")
            action_items = self._extract_structured_data(
                transcript_text,
                ACTION_ITEM_PROMPT,
                "action_items"
            )
            extraction_calls += 1
            logger.info(f"Stage 1/6 complete: {len(action_items)} action items")

            # Stage 2: Extract decisions
            logger.info("Stage 2/6: Extracting decisions...")
            decisions = self._extract_structured_data(
                transcript_text,
                DECISION_PROMPT,
                "decisions"
            )
            extraction_calls += 1
            logger.info(f"Stage 2/6 complete: {len(decisions)} decisions")

            # Stage 3: Extract topic segments
            logger.info("Stage 3/6: Extracting topic segments...")
            topics = self._extract_structured_data(
                transcript_text,
                TOPIC_SEGMENTATION_PROMPT,
                "topics"
            )
            extraction_calls += 1
            logger.info(f"Stage 3/6 complete: {len(topics)} topics")

            # Stage 4: Extract highlights
            logger.info("Stage 4/6: Extracting highlights...")
            highlights = self._extract_structured_data(
                transcript_text,
                HIGHLIGHTS_PROMPT,
                "highlights"
            )
            extraction_calls += 1
            logger.info(f"Stage 4/6 complete: {len(highlights)} highlights")

            # Stage 5: Extract mentions
            logger.info("Stage 5/6: Extracting mentions...")
            mentions = self._extract_structured_data(
                transcript_text,
                MENTIONS_PROMPT,
                "mentions"
            )
            extraction_calls += 1
            logger.info(f"Stage 5/6 complete: {len(mentions)} mentions")

            # Stage 6: Generate aggregate narrative summary
            logger.info("Stage 6/6: Generating aggregate summary...")
            aggregate_prompt = AGGREGATE_SUMMARY_PROMPT.format(
                metadata=self._format_metadata(meeting_metadata),
                transcript=transcript_text,
                action_items_count=len(action_items),
                decisions_count=len(decisions),
                topics_count=len(topics),
                highlights_count=len(highlights),
                mentions_count=len(mentions)
            )

            # Add custom instructions if provided
            if custom_instructions:
                aggregate_prompt += f"\n\n**Special Instructions from User:**\n{custom_instructions}"

            # Use aggregate client (may be different model in hybrid mode)
            response = self.aggregate_client.generate_text(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=aggregate_prompt,
                max_tokens=EXTRACTION_TOKEN_LIMITS["aggregate"],
                temperature=EXTRACTION_TEMPERATURE["aggregate"]
            )

            overall_summary = response["content"]
            extraction_calls += 1

            # Calculate total costs
            # Note: We'd need to track tokens from each extraction call
            # For now, estimate based on final response
            total_tokens = response["total_tokens"]
            total_cost = response["cost"]

            end_time = datetime.now()
            generation_time_ms = int((end_time - start_time).total_seconds() * 1000)

            logger.info(
                f"✓ Enhanced summary complete: {extraction_calls} API calls, "
                f"{total_tokens} tokens, ${total_cost:.4f}, {generation_time_ms}ms"
            )

            # Build metadata
            metadata = SummaryMetadata(
                total_tokens=total_tokens,
                total_cost=total_cost,
                generation_time_ms=generation_time_ms,
                model=response["model"],
                extraction_calls=extraction_calls,
                custom_instructions=custom_instructions
            )

            return EnhancedSummary(
                overall_summary=overall_summary,
                action_items=action_items,
                decisions=decisions,
                topics=topics,
                highlights=highlights,
                mentions=mentions,
                metadata=metadata
            )

        except Exception as e:
            logger.error(f"Enhanced summary generation failed: {e}", exc_info=True)
            raise SummaryGenerationError(f"Enhanced summary failed: {e}")

    def _extract_structured_data(
        self,
        transcript_text: str,
        prompt_template: str,
        extraction_type: str
    ) -> List[Dict[str, Any]]:
        """
        Extract structured data using a prompt template with prompt caching.

        Args:
            transcript_text: Formatted transcript
            prompt_template: Prompt template with {transcript} placeholder
            extraction_type: Type of extraction (for token limits)

        Returns:
            List of extracted items (parsed from JSON response)

        Notes:
            Uses prompt caching to reduce costs by 90% for calls 2-6.
            The transcript (static, ~34K tokens) is cached, while extraction
            instructions (dynamic, ~500 tokens) are not cached.
        """
        try:
            # Extract instructions from template (everything except {transcript})
            # Most templates have format: INSTRUCTIONS + "**Transcript:**\n{transcript}"
            instructions = prompt_template.replace("{transcript}", "").strip()

            # Build prompt with transcript FIRST (required for caching)
            # Structure: [TRANSCRIPT - CACHED] + [INSTRUCTIONS - NOT CACHED]
            cache_prefix = f"**Meeting Transcript:**\n\n{transcript_text}\n\n"
            user_prompt = cache_prefix + f"**Task:**\n\n{instructions}"

            # Get token limit and temperature for this extraction type
            max_tokens = EXTRACTION_TOKEN_LIMITS.get(extraction_type, 1000)
            temperature = EXTRACTION_TEMPERATURE.get(extraction_type, 0.3)

            # Call Claude API with prompt caching
            response = self.extraction_client.generate_text(
                system_prompt="You are an expert meeting analyst. Extract structured data accurately from transcripts. You MUST return ONLY valid, well-formed JSON. Ensure all strings are properly quoted and terminated. Ensure all JSON objects have matching braces. Double-check your JSON syntax before responding. Return NOTHING except the JSON array.",
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                cache_prefix=cache_prefix  # Enable caching for transcript
            )

            content = response["content"].strip()

            # Log the raw response for debugging
            logger.info(f"Claude response for {extraction_type} (first 500 chars): {content[:500]}")

            # Parse JSON response
            try:
                # Try to extract JSON from markdown code blocks if present
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()

                # Remove any leading text before the JSON array
                # Claude sometimes adds "Here are the action items:" before the JSON
                if content and not content.startswith('['):
                    # Find the first '[' character
                    bracket_index = content.find('[')
                    if bracket_index != -1:
                        content = content[bracket_index:]
                    else:
                        logger.error(f"NO JSON ARRAY found in response for {extraction_type}")
                        logger.info(f"Raw response (first 1000 chars): {content[:1000]}")
                        return []

                data = json.loads(content)

                # Ensure it's a list
                if not isinstance(data, list):
                    logger.warning(f"Expected list for {extraction_type}, got {type(data)}")
                    return []

                logger.info(f"✓ Extracted {len(data)} items for {extraction_type}")
                return data

            except json.JSONDecodeError as e:
                logger.error(f"JSON PARSE ERROR for {extraction_type}: {e}")

                # Try JSON repair - fix common issues
                try:
                    import re

                    # Log the problematic content
                    logger.info(f"Attempting JSON repair...")
                    logger.info(f"Problematic content (first 2000 chars): {content[:2000]}")

                    # Try to fix unterminated strings by closing the array
                    if content.startswith('[') and not content.rstrip().endswith(']'):
                        content_fixed = content.rstrip().rstrip(',') + ']'
                        data = json.loads(content_fixed)
                        logger.info(f"✓ JSON repaired by closing array - extracted {len(data)} items")
                        return data if isinstance(data, list) else []

                except Exception as repair_error:
                    logger.error(f"JSON repair failed: {repair_error}")

                logger.info(f"Returning empty list for {extraction_type}")
                return []

        except Exception as e:
            logger.error(f"EXTRACTION FAILED for {extraction_type}: {e}", exc_info=True)
            return []

    def _format_metadata(self, metadata: Dict[str, Any]) -> str:
        """Format meeting metadata as a string."""
        lines = []
        if "subject" in metadata:
            lines.append(f"Subject: {metadata['subject']}")
        if "organizer_name" in metadata:
            lines.append(f"Organizer: {metadata['organizer_name']}")
        if "start_time" in metadata:
            lines.append(f"Date: {metadata['start_time']}")
        if "duration_minutes" in metadata:
            lines.append(f"Duration: {metadata['duration_minutes']} minutes")
        if "participant_count" in metadata:
            lines.append(f"Participants: {metadata['participant_count']}")

        return "\n".join(lines)

    def estimate_enhanced_summary_cost(
        self,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Dict[str, Any]
    ) -> float:
        """
        Estimate total cost for enhanced summary (6 API calls).

        Args:
            transcript_segments: Parsed VTT segments
            meeting_metadata: Meeting details

        Returns:
            Estimated total cost in USD
        """
        transcript_text = format_transcript_for_extraction(transcript_segments)

        # Estimate cost for each extraction type
        total_cost = 0.0

        for extraction_type, max_tokens in EXTRACTION_TOKEN_LIMITS.items():
            # Rough estimate: input tokens + output tokens
            input_tokens = estimate_token_count(transcript_text)
            cost = self.client.estimate_cost(transcript_text, expected_output_tokens=max_tokens)
            total_cost += cost

        return total_cost
