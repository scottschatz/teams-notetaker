"""
Meeting Summarizer

Generates AI-powered summaries of meeting transcripts using Claude API.
Uses prompt templates from prompts.py and handles token limits.

Includes both basic MeetingSummarizer and enhanced EnhancedMeetingSummarizer
with multi-stage extraction (action items, decisions, topics, highlights, mentions).
"""

import logging
import json
import time
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
    KEY_NUMBERS_PROMPT,
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
        key_numbers: List of extracted quantitative metrics
        ai_answerable_questions: List of questions AI can help answer with responses
        metadata: Summary generation metadata
    """
    overall_summary: str  # Markdown narrative
    action_items: List[Dict[str, Any]]
    decisions: List[Dict[str, Any]]
    topics: List[Dict[str, Any]]
    highlights: List[Dict[str, Any]]
    mentions: List[Dict[str, Any]]
    key_numbers: List[Dict[str, Any]]
    ai_answerable_questions: List[Dict[str, Any]]
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
            "key_numbers": self.key_numbers,
            "ai_answerable_questions": self.ai_answerable_questions,
            "metadata": asdict(self.metadata)
        }


class EnhancedMeetingSummarizer:
    """
    Multi-stage meeting summarizer with structured data extraction.

    Uses 7 separate Claude API calls:
    1. Extract action items
    2. Extract decisions
    3. Extract topic segments
    4. Extract highlights
    5. Extract mentions
    6. Extract key numbers (financial/quantitative metrics)
    7. Generate aggregate narrative summary

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
        print(f"Found {len(result.key_numbers)} metrics")
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
            logger.info("Stage 1/5: Extracting action items...")
            action_items = self._extract_structured_data(
                transcript_text,
                ACTION_ITEM_PROMPT,
                "action_items"
            )
            extraction_calls += 1
            logger.info(f"Stage 1/5 complete: {len(action_items)} action items")

            # Stage 2: Extract decisions
            logger.info("Stage 2/5: Extracting decisions...")
            decisions = self._extract_structured_data(
                transcript_text,
                DECISION_PROMPT,
                "decisions"
            )
            extraction_calls += 1
            logger.info(f"Stage 2/5 complete: {len(decisions)} decisions")

            # Stage 3: Extract highlights (was stage 4)
            logger.info("Stage 3/5: Extracting highlights...")
            highlights = self._extract_structured_data(
                transcript_text,
                HIGHLIGHTS_PROMPT,
                "highlights"
            )
            extraction_calls += 1
            logger.info(f"Stage 3/5 complete: {len(highlights)} highlights")

            # Stage 4: Extract key numbers (was stage 6)
            logger.info("Stage 4/5: Extracting key numbers...")
            key_numbers = self._extract_structured_data(
                transcript_text,
                KEY_NUMBERS_PROMPT,
                "key_numbers"
            )
            extraction_calls += 1
            logger.info(f"Stage 4/5 complete: {len(key_numbers)} key numbers")

            # Topics and mentions removed - not used in email template
            topics = []  # Placeholder for backward compatibility
            mentions = []  # Placeholder for backward compatibility

            # Stage 5: Generate aggregate narrative summary (was stage 7)
            logger.info("Stage 5/5: Generating aggregate summary...")

            # Build transcript cache prefix (will be cached for 90% cost savings)
            cache_prefix = f"**Meeting Transcript:**\n\n{transcript_text}\n\n"

            # Build aggregate instructions WITHOUT transcript (not cached)
            aggregate_instructions = AGGREGATE_SUMMARY_PROMPT.format(
                metadata=self._format_metadata(meeting_metadata),
                transcript="",  # Transcript is in cache_prefix
                action_items_count=len(action_items),
                decisions_count=len(decisions),
                topics_count=0,  # Not extracted anymore
                highlights_count=len(highlights),
                key_numbers_count=len(key_numbers),
                mentions_count=0  # Not extracted anymore
            ).replace("**Transcript:**\n\n\n", "")  # Remove empty transcript placeholder

            # Add custom instructions if provided
            if custom_instructions:
                aggregate_instructions += f"\n\n**Special Instructions from User:**\n{custom_instructions}"

            # Combine: [CACHED TRANSCRIPT] + [DYNAMIC INSTRUCTIONS]
            aggregate_prompt = cache_prefix + aggregate_instructions

            # Use aggregate client with prompt caching (90% savings on transcript tokens)
            response = self.aggregate_client.generate_text(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=aggregate_prompt,
                max_tokens=EXTRACTION_TOKEN_LIMITS["aggregate"],
                temperature=EXTRACTION_TEMPERATURE["aggregate"],
                cache_prefix=cache_prefix  # Enable caching for transcript!
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
                key_numbers=key_numbers,
                ai_answerable_questions=[],  # Not extracted in multi-stage
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


class SingleCallSummarizer:
    """
    Single API call meeting summarizer with Gemini primary + Haiku fallback.

    MODEL HIERARCHY:
    1. PRIMARY: Gemini 3 Flash (gemini-2.0-flash)
       - 48% cheaper than Haiku ($0.50/$3.00 vs $1.00/$5.00 per MTok)
       - Uses optimized prompt from gemini_prompt.py
       - Requires GOOGLE_API_KEY environment variable

    2. FALLBACK: Claude Haiku 4.5
       - Used when Gemini fails (API errors, quota, invalid JSON)
       - Uses haiku prompt from single_call_prompt.py
       - Requires CLAUDE_API_KEY (already configured)

    WHEN FALLBACK IS TRIGGERED:
    - GOOGLE_API_KEY not set → immediate fallback
    - Gemini API error (quota, rate limit, outage) → fallback after retries
    - Gemini returns invalid/unparseable JSON → fallback
    - Any unexpected Gemini exception → fallback

    Returns same EnhancedSummary structure as EnhancedMeetingSummarizer for compatibility.
    """

    # ============================================================================
    # MODEL TOGGLE - Set to True to use Gemini as primary, False to use Haiku only
    # ============================================================================
    # CURRENT STATUS: Using Haiku only (Gemini disabled)
    # REASON: Haiku produces superior quality summaries with better:
    #   - Duration extraction (Gemini showed "None minutes")
    #   - Speaker participation stats (word counts, speaking time)
    #   - Richer discussion notes with more detail
    # TO RE-ENABLE GEMINI: Set USE_GEMINI_PRIMARY = True
    # ============================================================================
    USE_GEMINI_PRIMARY = False

    def __init__(self, claude_config: ClaudeConfig):
        """
        Initialize with Claude config (Haiku fallback).

        Gemini is initialized lazily on first use if GOOGLE_API_KEY is available.
        """
        self.claude_config = claude_config
        self.claude_client = ClaudeClient(claude_config)
        self._gemini_client = None  # Lazy initialization
        self._gemini_available = None  # None = not checked yet

        logger.info(
            f"Initialized SingleCallSummarizer (primary: Gemini 3 Flash, "
            f"fallback: {claude_config.model})"
        )

    def _get_gemini_client(self):
        """
        Lazy initialization of Gemini client.

        Returns:
            GeminiClient instance, or None if GOOGLE_API_KEY not set
        """
        import os
        if self._gemini_available is None:
            # First check - see if API key is available
            if os.getenv("GOOGLE_API_KEY"):
                try:
                    from .gemini_client import GeminiClient
                    self._gemini_client = GeminiClient()
                    self._gemini_available = True
                    logger.info("Gemini client initialized successfully")
                except Exception as e:
                    logger.warning(f"Failed to initialize Gemini client: {e}")
                    self._gemini_available = False
            else:
                logger.info("GOOGLE_API_KEY not set, using Haiku fallback only")
                self._gemini_available = False

        return self._gemini_client if self._gemini_available else None

    def generate_enhanced_summary(
        self,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Optional[Dict[str, Any]] = None,
        custom_instructions: Optional[str] = None
    ) -> EnhancedSummary:
        """
        Generate complete meeting summary with Gemini (primary) or Haiku (fallback).

        Tries Gemini first for 48% cost savings. Falls back to Haiku on any error.

        Args:
            transcript_segments: List of transcript segments with speaker/text/timestamp
            meeting_metadata: Optional meeting metadata (title, participants, duration)
            custom_instructions: Optional custom extraction instructions

        Returns:
            EnhancedSummary with all extracted data and metadata
        """
        # Check if Gemini is enabled via class toggle
        if self.USE_GEMINI_PRIMARY:
            # Try Gemini first
            gemini_client = self._get_gemini_client()
            if gemini_client:
                try:
                    return self._generate_with_gemini(
                        gemini_client,
                        transcript_segments,
                        meeting_metadata,
                        custom_instructions
                    )
                except Exception as e:
                    logger.warning(f"Gemini failed, falling back to Haiku: {e}")
                    # Fall through to Haiku
        else:
            logger.info("Gemini disabled (USE_GEMINI_PRIMARY=False), using Haiku directly")

        # Use Haiku (either as fallback or primary when Gemini is disabled)
        return self._generate_with_haiku(
            transcript_segments,
            meeting_metadata,
            custom_instructions
        )

    def _generate_with_gemini(
        self,
        gemini_client,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Optional[Dict[str, Any]] = None,
        custom_instructions: Optional[str] = None
    ) -> EnhancedSummary:
        """
        Generate summary using Gemini 3 Flash.

        Uses the Gemini-optimized prompt from gemini_prompt.py.
        """
        start_time = time.time()

        # Format transcript
        transcript_text = self._format_transcript(transcript_segments)

        # Extract participant names from metadata for correct spelling
        participant_names = []
        if meeting_metadata and meeting_metadata.get("participant_names"):
            participant_names = meeting_metadata["participant_names"]
        participant_names_str = "\n".join(f"- {name}" for name in participant_names) if participant_names else "(No participant list available)"

        # Load Gemini-optimized prompt
        from .prompts.gemini_prompt import GEMINI_SINGLE_CALL_PROMPT

        # Build user prompt
        user_prompt = GEMINI_SINGLE_CALL_PROMPT.format(
            transcript=transcript_text,
            participant_names=participant_names_str
        )

        # Add custom instructions if provided
        if custom_instructions:
            user_prompt = f"{custom_instructions}\n\n{user_prompt}"

        # System prompt for JSON-only output
        system_prompt = (
            "You are an expert meeting analyst. Extract structured data accurately from transcripts. "
            "You MUST return ONLY valid, well-formed JSON. Ensure all strings are properly quoted "
            "and terminated. Ensure all JSON objects have matching braces. Double-check your JSON "
            "syntax before responding. Return NOTHING except the JSON object. "
            "Do NOT include the transcript in your response. "
            "Preserve ALL markdown formatting including bold participant names (**Name**) and bold subheadings."
        )

        # Make Gemini API call
        logger.info("Calling Gemini API for single-call extraction")
        response = gemini_client.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=8000,
            temperature=0.5,
        )

        # Parse JSON response
        content = response["content"]
        try:
            data = self._parse_json_response(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON response: {e}")
            logger.error(f"Response content: {content[:500]}...")
            raise ValueError(f"Invalid JSON response from Gemini API: {e}")

        # Extract and build result
        return self._build_enhanced_summary(data, response, start_time, "gemini_single_call")

    def _generate_with_haiku(
        self,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Optional[Dict[str, Any]] = None,
        custom_instructions: Optional[str] = None
    ) -> EnhancedSummary:
        """
        Generate summary using Claude Haiku (fallback).

        Uses the Haiku-tuned prompt from single_call_prompt.py.
        """
        start_time = time.time()

        # Format transcript
        transcript_text = self._format_transcript(transcript_segments)

        # Extract participant names from metadata for correct spelling
        participant_names = []
        if meeting_metadata and meeting_metadata.get("participant_names"):
            participant_names = meeting_metadata["participant_names"]
        participant_names_str = "\n".join(f"- {name}" for name in participant_names) if participant_names else "(No participant list available)"

        # Extract meeting date for resolving relative deadlines (e.g., "Tomorrow" → actual date)
        meeting_date_str = "Unknown date"
        if meeting_metadata and meeting_metadata.get("start_time"):
            try:
                from datetime import datetime
                start_time_val = meeting_metadata["start_time"]
                if isinstance(start_time_val, str):
                    # Parse ISO format
                    dt = datetime.fromisoformat(start_time_val.replace("Z", "+00:00"))
                else:
                    dt = start_time_val
                meeting_date_str = dt.strftime("%A, %B %d, %Y")  # e.g., "Sunday, December 22, 2025"
            except Exception as e:
                logger.debug(f"Could not parse meeting date: {e}")
                meeting_date_str = "Unknown date"

        # Load Haiku fallback prompt
        from .prompts.single_call_prompt import SINGLE_CALL_COMPREHENSIVE_PROMPT

        # Build user prompt
        user_prompt = SINGLE_CALL_COMPREHENSIVE_PROMPT.format(
            transcript=transcript_text,
            participant_names=participant_names_str,
            meeting_date=meeting_date_str
        )

        # Add custom instructions if provided
        if custom_instructions:
            user_prompt = f"{custom_instructions}\n\n{user_prompt}"

        # System prompt for JSON-only output with formatting preservation
        system_prompt = (
            "You are an expert meeting analyst. Extract structured data accurately from transcripts. "
            "You MUST return ONLY valid, well-formed JSON. Ensure all strings are properly quoted "
            "and terminated. Ensure all JSON objects have matching braces. Double-check your JSON "
            "syntax before responding. Return NOTHING except the JSON object. "
            "Do NOT include the transcript in your response. "
            "Preserve ALL markdown formatting including bold participant names (**Name**) and bold subheadings."
        )

        # Make Claude API call
        logger.info("Calling Claude Haiku API for single-call extraction (fallback)")
        response = self.claude_client.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=16000,  # Increased for RAG metadata fields
            temperature=0.5,
        )

        # Parse JSON response
        content = response["content"]
        try:
            data = self._parse_json_response(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Haiku JSON response: {e}")
            logger.error(f"Response content: {content[:500]}...")
            raise ValueError(f"Invalid JSON response from Claude API: {e}")

        # Extract and build result
        return self._build_enhanced_summary(data, response, start_time, "haiku_fallback")

    def _build_enhanced_summary(
        self,
        data: dict,
        response: dict,
        start_time: float,
        approach: str
    ) -> EnhancedSummary:
        """
        Build EnhancedSummary from parsed JSON data.

        Args:
            data: Parsed JSON response
            response: Raw API response with token/cost info
            start_time: When generation started (for timing)
            approach: "gemini_single_call" or "haiku_fallback"

        Returns:
            EnhancedSummary object
        """
        # Extract fields with validation
        action_items = data.get("action_items", [])
        decisions = data.get("decisions", [])
        highlights = data.get("highlights", [])
        key_numbers = data.get("key_numbers", [])
        ai_answerable_questions = data.get("ai_answerable_questions", [])
        executive_summary = data.get("executive_summary", "")
        discussion_notes = data.get("discussion_notes", "")

        # Log discussion notes word count for monitoring
        word_count = len(discussion_notes.split())
        logger.info(f"Discussion notes word count: {word_count} ({approach})")

        # Build metadata
        generation_time = int((time.time() - start_time) * 1000)
        metadata = {
            "total_tokens": response["total_tokens"],
            "total_cost": response["cost"],
            "extraction_calls": 1,  # Single call
            "generation_time_ms": generation_time,
            "approach": approach,
            "model": response["model"],
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
            "discussion_notes_word_count": word_count,
        }

        # Combine executive summary + discussion notes
        overall_summary = f"## Executive Summary\n\n{executive_summary}\n\n## Discussion Notes\n\n{discussion_notes}"

        # Return EnhancedSummary (same structure as multi-stage)
        return EnhancedSummary(
            action_items=action_items,
            decisions=decisions,
            topics=[],  # Not extracted in single-call
            highlights=highlights,
            mentions=[],  # Not extracted in single-call
            key_numbers=key_numbers,
            ai_answerable_questions=ai_answerable_questions,
            overall_summary=overall_summary,
            metadata=metadata
        )

    def _format_transcript(self, segments: List[Dict[str, Any]]) -> str:
        """Format transcript segments into readable text."""
        formatted = []
        for segment in segments:
            speaker = segment.get("speaker", "Unknown")
            text = segment.get("text", "")
            timestamp = segment.get("timestamp", "")
            formatted.append(f"[{timestamp}] {speaker}: {text}")
        return "\n\n".join(formatted)

    def _parse_json_response(self, content: str) -> dict:
        """
        Parse JSON response from Claude, handling edge cases.

        Similar to EnhancedMeetingSummarizer's JSON parsing logic.
        """
        # Remove markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        # Remove leading text before JSON object
        if content and not content.startswith('{'):
            brace_index = content.find('{')
            if brace_index != -1:
                content = content[brace_index:]

        # Parse JSON
        return json.loads(content)

    def extract_classification_metadata(
        self,
        transcript_segments: List[Dict[str, Any]],
        meeting_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract enterprise intelligence classification metadata from meeting transcript.

        This is a separate AI call that extracts metadata for:
        - Meeting classification (type, category, seniority)
        - Sentiment and tone analysis
        - Counts and metrics
        - Topics and entities
        - Structured data (concerns, blockers, market intelligence)
        - External detection (clients, competitors)
        - Quick filtering flags

        Uses prompt caching (transcript is cached from main summary call).

        Args:
            transcript_segments: List of transcript segments with speaker/text/timestamp
            meeting_metadata: Optional meeting metadata (for participant names)

        Returns:
            Dict with classification metadata ready to save to Summary model
        """
        start_time = time.time()

        # Format transcript
        transcript_text = self._format_transcript(transcript_segments)

        # Extract participant names from metadata for context
        participant_names = []
        if meeting_metadata and meeting_metadata.get("participant_names"):
            participant_names = meeting_metadata["participant_names"]
        participant_names_str = "\n".join(f"- {name}" for name in participant_names) if participant_names else "(No participant list available)"

        # Load classification prompt
        from .prompts.classification_prompt import CLASSIFICATION_PROMPT

        # Build user prompt
        user_prompt = CLASSIFICATION_PROMPT.format(
            transcript=transcript_text,
            participant_names=participant_names_str
        )

        # System prompt for JSON-only output
        system_prompt = (
            "You are an expert meeting analyst specializing in meeting classification and metadata extraction. "
            "You MUST return ONLY valid, well-formed JSON. Ensure all strings are properly quoted "
            "and terminated. Ensure all JSON objects have matching braces. Double-check your JSON "
            "syntax before responding. Return NOTHING except the JSON object."
        )

        # Make Claude API call (uses Haiku for cost efficiency)
        logger.info("Calling Claude Haiku API for classification metadata extraction")
        try:
            response = self.claude_client.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4000,  # Classification response is smaller than full summary
                temperature=0.3,  # Lower temperature for more consistent classification
            )

            # Parse JSON response
            content = response["content"]
            try:
                data = self._parse_json_response(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse classification JSON response: {e}")
                logger.error(f"Response content: {content[:500]}...")
                return {}

            # Extract and flatten the nested structure for database storage
            result = self._flatten_classification_data(data)

            generation_time = int((time.time() - start_time) * 1000)
            logger.info(
                f"Classification extraction complete: {response['total_tokens']} tokens, "
                f"${response['cost']:.4f}, {generation_time}ms"
            )

            return result

        except Exception as e:
            logger.error(f"Classification extraction failed: {e}", exc_info=True)
            return {}

    def _flatten_classification_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flatten nested classification response for database storage.

        Takes the structured JSON response and maps it to flat database columns.

        Args:
            data: Parsed JSON response with nested structure

        Returns:
            Flat dict with column-friendly keys
        """
        result = {}

        # Classification fields
        classification = data.get("classification", {})
        result["meeting_type"] = classification.get("meeting_type")
        result["meeting_category"] = classification.get("meeting_category")
        result["seniority_level"] = classification.get("seniority_level")
        result["department_context"] = classification.get("department_context")
        result["is_onboarding"] = classification.get("is_onboarding", False)
        result["is_coaching"] = classification.get("is_coaching", False)
        result["is_sales_meeting"] = classification.get("is_sales_meeting", False)
        result["is_support_call"] = classification.get("is_support_call", False)

        # Sentiment fields
        sentiment = data.get("sentiment", {})
        result["overall_sentiment"] = sentiment.get("overall_sentiment")
        result["urgency_level"] = sentiment.get("urgency_level")
        result["consensus_level"] = sentiment.get("consensus_level")
        result["meeting_effectiveness"] = sentiment.get("meeting_effectiveness")
        result["communication_style"] = sentiment.get("communication_style")
        result["energy_level"] = sentiment.get("energy_level")

        # Counts fields
        counts = data.get("counts", {})
        result["action_item_count"] = counts.get("action_item_count")
        result["decision_count"] = counts.get("decision_count")
        result["open_question_count"] = counts.get("open_question_count")
        result["blocker_count"] = counts.get("blocker_count")
        result["follow_up_required"] = counts.get("follow_up_required", False)
        result["has_concerns"] = counts.get("blocker_count", 0) > 0 or len(data.get("structured_data", {}).get("concerns", [])) > 0

        # Topics fields (JSONB)
        topics = data.get("topics", {})
        result["topics_discussed"] = topics.get("topics_discussed")
        result["projects_mentioned"] = topics.get("projects_mentioned")
        result["products_mentioned"] = topics.get("products_mentioned")
        result["technologies_discussed"] = topics.get("technologies_discussed")
        result["people_mentioned"] = topics.get("people_mentioned")
        result["deadlines_mentioned"] = topics.get("deadlines_mentioned")
        result["financial_mentions"] = topics.get("financial_mentions")

        # Structured data fields (JSONB)
        structured = data.get("structured_data", {})
        result["concerns_json"] = structured.get("concerns")
        result["blockers_json"] = structured.get("blockers")
        result["market_intelligence_json"] = structured.get("market_intelligence")
        result["training_content_json"] = structured.get("training_content")

        # External detection fields
        external = data.get("external_detection", {})
        result["has_external_participants"] = external.get("has_external_participants", False)
        result["external_company_names"] = external.get("external_company_names")
        result["client_names"] = external.get("client_names")
        result["competitor_names"] = external.get("competitor_names")

        # Flag fields
        flags = data.get("flags", {})
        result["has_financial_discussion"] = flags.get("has_financial_discussion", False)
        result["has_deadline_pressure"] = flags.get("has_deadline_pressure", False)
        result["has_escalation"] = flags.get("has_escalation", False)
        result["has_customer_complaint"] = flags.get("has_customer_complaint", False)
        result["has_technical_discussion"] = flags.get("has_technical_discussion", False)
        result["is_confidential"] = flags.get("is_confidential", False)

        return result
