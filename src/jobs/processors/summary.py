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
from ...ai.summarizer import MeetingSummarizer
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
        - input_tokens: Claude API input tokens
        - output_tokens: Claude API output tokens
        - total_tokens: Total tokens
        - cost: Estimated cost in USD
        - model: Claude model used
        - generation_time_ms: Generation time
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

        # Initialize Claude client and summarizer
        self.claude_client = ClaudeClient(config.claude)
        self.summarizer = MeetingSummarizer(config.claude)

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

            # Generate summary
            self._log_progress(job, f"Calling Claude API to generate summary")

            try:
                result = self.summarizer.summarize_meeting(
                    transcript=transcript.parsed_content,  # Pass raw segments, not formatted string
                    meeting_metadata=meeting_metadata,
                    summary_type="full",
                    max_tokens=self.config.app.summary_max_tokens
                )

                summary_text = result["summary"]
                input_tokens = result["input_tokens"]
                output_tokens = result["output_tokens"]
                total_tokens = result["total_tokens"]
                cost = result["cost"]
                model = result["model"]
                generation_time_ms = result["generation_time_ms"]
                truncated = result.get("truncated", False)

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

                # Save summary to database
                summary = Summary(
                    meeting_id=meeting_id,
                    transcript_id=transcript.id,
                    summary_text=summary_text,
                    summary_html=summary_html,
                    model=model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=total_tokens,
                    generation_time_ms=generation_time_ms
                )
                session.add(summary)
                session.flush()

                summary_id = summary.id

                # Update meeting
                meeting.has_summary = True

                session.commit()

                self._log_progress(job, f"✓ Summary saved to database (id: {summary_id})")

                # Create preview (first 200 chars)
                summary_preview = (
                    summary_text[:200] + "..." if len(summary_text) > 200 else summary_text
                )

                return self._create_output_data(
                    success=True,
                    message=f"Summary generated successfully ({output_tokens} tokens, ${cost:.4f})",
                    summary_id=summary_id,
                    summary_preview=summary_preview,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost=cost,
                    model=model,
                    generation_time_ms=generation_time_ms,
                    truncated=truncated
                )

            except ClaudeAPIError as e:
                self._log_progress(job, f"Claude API error: {e}", "error")
                raise SummaryGenerationError(f"Claude API failed: {e}")

            except Exception as e:
                self._log_progress(job, f"Summary generation failed: {e}", "error")
                raise SummaryGenerationError(f"Summary generation failed: {e}")
