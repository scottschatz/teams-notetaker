"""
Summary Processor

Generates AI-powered meeting summaries using Claude API.
Second processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
from typing import Dict, Any
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...ai.claude_client import ClaudeClient
from ...ai.summarizer import MeetingSummarizer, EnhancedMeetingSummarizer, EnhancedSummary
from ...utils.vtt_parser import format_transcript_for_summary
from ...core.database import Summary, Transcript
from ...core.exceptions import SummaryGenerationError, ClaudeAPIError


logger = logging.getLogger(__name__)


@register_processor("generate_summary")
class SummaryProcessor(BaseProcessor):
    """
    Generates AI summaries of meeting transcripts.

    Input (job.input_data):
        - meeting_id: Database meeting ID

    Output (job.output_data):
        - success: bool
        - summary_id: Database summary ID
        - summary_preview: First 200 chars of summary
        - version: Summary version number (1 for initial, 2+ for re-summarizations)
        - input_tokens: Claude API input tokens
        - output_tokens: Claude API output tokens
        - total_tokens: Total tokens
        - cost: Estimated cost in USD
        - model: Claude model used
        - generation_time_ms: Generation time
        - extraction_calls: Number of API calls made (6 for enhanced)
        - action_items_count: Number of extracted action items
        - decisions_count: Number of extracted decisions
        - topics_count: Number of topic segments
        - highlights_count: Number of key moments
        - mentions_count: Number of person mentions
        - custom_instructions: User-provided instructions (if any)
        - message: Status message

    Updates:
        - meetings.has_summary = True
        - Creates summary record in database

    Errors:
        - SummaryGenerationError: Summary generation failed
        - ClaudeAPIError: Claude API request failed
    """

    def __init__(self, db, config):
        """
        Initialize summary processor.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
        """
        super().__init__(db, config)

        # Initialize Claude client and enhanced summarizer
        self.claude_client = ClaudeClient(config.claude)
        self.summarizer = EnhancedMeetingSummarizer(config.claude)

    async def process(self, job) -> Dict[str, Any]:
        """
        Process generate_summary job.

        Args:
            job: JobQueue object

        Returns:
            Output data dictionary
        """
        # Validate input
        self._validate_job_input(job, required_fields=["meeting_id"])

        meeting_id = job.input_data["meeting_id"]

        self._log_progress(job, f"Generating summary for meeting {meeting_id}")

        # Get meeting and transcript from database
        meeting = self._get_meeting(meeting_id)

        with self.db.get_session() as session:
            # Check if summary already exists
            existing_summary = session.query(Summary).filter_by(meeting_id=meeting_id).first()
            if existing_summary:
                self._log_progress(job, "Summary already exists, skipping", "warning")
                return self._create_output_data(
                    success=True,
                    message="Summary already exists",
                    summary_id=existing_summary.id,
                    cached=True
                )

            # Get transcript
            transcript = session.query(Transcript).filter_by(meeting_id=meeting_id).first()
            if not transcript:
                raise SummaryGenerationError(f"No transcript found for meeting {meeting_id}")

            self._log_progress(
                job,
                f"Found transcript: {transcript.word_count} words, {transcript.speaker_count} speakers"
            )

            # Build meeting metadata
            meeting_metadata = {
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "start_time": meeting.start_time.isoformat() if meeting.start_time else "",
                "end_time": meeting.end_time.isoformat() if meeting.end_time else "",
                "duration_minutes": meeting.duration_minutes,
                "participant_count": meeting.participant_count
            }

            # Generate enhanced summary with structured extractions
            self._log_progress(job, f"Calling Claude API to generate enhanced summary (6-stage extraction)")

            try:
                # Check if this is a re-summarization with custom instructions
                custom_instructions = job.input_data.get("custom_instructions")
                if custom_instructions:
                    self._log_progress(job, f"Using custom instructions: {custom_instructions}")

                # Generate enhanced summary
                enhanced_result: EnhancedSummary = self.summarizer.generate_enhanced_summary(
                    transcript_segments=transcript.parsed_content,  # Pass raw segments
                    meeting_metadata=meeting_metadata,
                    custom_instructions=custom_instructions
                )

                summary_text = enhanced_result.overall_summary
                action_items = enhanced_result.action_items
                decisions = enhanced_result.decisions
                topics = enhanced_result.topics
                highlights = enhanced_result.highlights
                mentions = enhanced_result.mentions
                metadata = enhanced_result.metadata

                input_tokens = metadata.total_tokens  # Approximate
                output_tokens = metadata.total_tokens  # We don't track separately yet
                total_tokens = metadata.total_tokens
                cost = metadata.total_cost
                model = metadata.model
                generation_time_ms = metadata.generation_time_ms
                truncated = False  # Enhanced summarizer doesn't truncate

                self._log_progress(
                    job,
                    f"✓ Summary generated: {output_tokens} tokens, "
                    f"${cost:.4f}, {generation_time_ms}ms"
                )

                if truncated:
                    self._log_progress(
                        job,
                        "Warning: Transcript was truncated due to token limits",
                        "warning"
                    )

                # Convert markdown to HTML (for email display)
                import markdown2
                summary_html = markdown2.markdown(
                    summary_text,
                    extras=["tables", "fenced-code-blocks", "code-friendly"]
                )

                # Determine version for this summary
                version = job.input_data.get("version", 1)

                # Save enhanced summary to database
                summary = Summary(
                    meeting_id=meeting_id,
                    transcript_id=transcript.id,
                    summary_text=summary_text,
                    summary_html=summary_html,
                    model=model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=total_tokens,
                    generation_time_ms=generation_time_ms,
                    # Enhanced summary data (NEW)
                    action_items_json=action_items,
                    decisions_json=decisions,
                    topics_json=topics,
                    highlights_json=highlights,
                    mentions_json=mentions,
                    version=version,
                    custom_instructions=custom_instructions
                )
                session.add(summary)
                session.flush()

                summary_id = summary.id

                # If this is a re-summarization, mark previous version as superseded
                if version > 1:
                    previous_summary = session.query(Summary).filter_by(
                        meeting_id=meeting_id,
                        version=version - 1
                    ).first()
                    if previous_summary:
                        previous_summary.superseded_by = summary_id

                # Update meeting
                meeting.has_summary = True

                session.commit()

                self._log_progress(
                    job,
                    f"✓ Enhanced summary saved (v{version}): "
                    f"{len(action_items)} actions, {len(decisions)} decisions, "
                    f"{len(topics)} topics, {len(highlights)} highlights, "
                    f"{len(mentions)} mentions"
                )

                # Create preview (first 200 chars)
                summary_preview = (
                    summary_text[:200] + "..." if len(summary_text) > 200 else summary_text
                )

                return self._create_output_data(
                    success=True,
                    message=f"Enhanced summary generated (v{version}): {len(action_items)} actions, {len(decisions)} decisions, ${cost:.4f}",
                    summary_id=summary_id,
                    summary_preview=summary_preview,
                    version=version,
                    # Token and cost info
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost=cost,
                    model=model,
                    generation_time_ms=generation_time_ms,
                    extraction_calls=metadata.extraction_calls,
                    # Structured data counts
                    action_items_count=len(action_items),
                    decisions_count=len(decisions),
                    topics_count=len(topics),
                    highlights_count=len(highlights),
                    mentions_count=len(mentions),
                    custom_instructions=custom_instructions
                )

            except ClaudeAPIError as e:
                self._log_progress(job, f"Claude API error: {e}", "error")
                raise SummaryGenerationError(f"Claude API failed: {e}")

            except Exception as e:
                self._log_progress(job, f"Summary generation failed: {e}", "error")
                raise SummaryGenerationError(f"Summary generation failed: {e}")
