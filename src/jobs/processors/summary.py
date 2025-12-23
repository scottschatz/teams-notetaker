"""
Summary Processor

Generates AI-powered meeting summaries using Claude API.
Second processor in the job chain (fetch_transcript → generate_summary → distribute).
"""

import logging
import asyncio
from typing import Dict, Any
from datetime import datetime

from ..processors.base import BaseProcessor, register_processor
from ...ai.claude_client import ClaudeClient
from ...ai.summarizer import MeetingSummarizer, EnhancedMeetingSummarizer, SingleCallSummarizer, EnhancedSummary
from ...utils.vtt_parser import format_transcript_for_summary
from ...core.database import Summary, Transcript, Meeting, MeetingParticipant
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

        # Initialize Claude client
        self.claude_client = ClaudeClient(config.claude)

        # ALL-SONNET APPROACH: Use Sonnet 4.5 for all 6 calls (extraction + aggregate)
        # Prioritizes QUALITY over cost savings for accurate, detailed summaries
        # Cost: ~$0.12/meeting vs $0.055/meeting hybrid (but much better quality)

        # Use the model from config (config.yaml or environment)
        # Default is claude-sonnet-4-20250514, can be overridden to claude-haiku-4-5-20251001 for cost savings
        model_config = config.claude
        logger.debug(f"Using Claude model: {model_config.model}")

        # Choose summarizer based on config flag
        if config.app.use_single_call_summarization:
            logger.info(f"Using single-call summarization with {model_config.model}")
            self.summarizer = SingleCallSummarizer(model_config)
        else:
            logger.info(f"Using multi-stage summarization with {model_config.model}")
            self.summarizer = EnhancedMeetingSummarizer(model_config, model_config)

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

        # Determine version for this summary (default to 1, or use input_data version)
        requested_version = job.input_data.get("version", 1)

        with self.db.get_session() as session:
            # Check if summary with THIS VERSION already exists
            existing_summary = session.query(Summary).filter_by(
                meeting_id=meeting_id,
                version=requested_version
            ).first()
            if existing_summary:
                self._log_progress(job, f"Summary v{requested_version} already exists, skipping", "warning")
                return self._create_output_data(
                    success=True,
                    message=f"Summary v{requested_version} already exists",
                    summary_id=existing_summary.id,
                    version=requested_version,
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

            # Fetch participant names for correct spelling in summary
            participants = session.query(MeetingParticipant).filter_by(meeting_id=meeting_id).all()
            participant_names = [p.display_name for p in participants if p.display_name]

            # Build meeting metadata
            meeting_metadata = {
                "subject": meeting.subject,
                "organizer_name": meeting.organizer_name,
                "start_time": meeting.start_time.isoformat() if meeting.start_time else "",
                "end_time": meeting.end_time.isoformat() if meeting.end_time else "",
                "duration_minutes": meeting.duration_minutes,
                "participant_count": meeting.participant_count,
                "participant_names": participant_names  # For correct name spelling
            }

            # Generate enhanced summary with structured extractions
            self._log_progress(job, f"Calling Claude API to generate enhanced summary (6-stage extraction)")

            try:
                # Check if this is a re-summarization with custom instructions
                custom_instructions = job.input_data.get("custom_instructions")
                if custom_instructions:
                    self._log_progress(job, f"Using custom instructions: {custom_instructions}")

                # Generate enhanced summary with optional review pass
                # Review is triggered for: large meetings (25+ participants), executives, financial discussions
                # Uses Haiku review for 25-49 participants, Sonnet review for 50+ or executives
                loop = asyncio.get_event_loop()
                enhanced_result: EnhancedSummary = await loop.run_in_executor(
                    None,
                    lambda: self.summarizer.generate_enhanced_summary_with_review(
                        transcript_segments=transcript.parsed_content,  # Pass raw segments
                        meeting_metadata=meeting_metadata,
                        custom_instructions=custom_instructions
                    )
                )

                summary_text = enhanced_result.overall_summary
                action_items = enhanced_result.action_items
                decisions = enhanced_result.decisions
                topics = enhanced_result.topics
                highlights = enhanced_result.highlights
                mentions = enhanced_result.mentions
                key_numbers = enhanced_result.key_numbers  # FIX: Extract key_numbers
                ai_answerable_questions = enhanced_result.ai_answerable_questions
                topics_to_explore = enhanced_result.topics_to_explore
                metadata = enhanced_result.metadata

                input_tokens = metadata.get("input_tokens", 0)
                output_tokens = metadata.get("output_tokens", 0)
                total_tokens = metadata.get("total_tokens", input_tokens + output_tokens)
                generation_cost = metadata.get("total_cost", 0.0)
                model = metadata.get("model", "unknown")
                generation_time_ms = metadata.get("generation_time_ms", 0)
                truncated = False  # Enhanced summarizer doesn't truncate

                # Extract approach and additional metadata
                approach = metadata.get("approach", "multi_stage")
                extraction_calls = metadata.get("extraction_calls", 5)

                # Extract review metadata (if two-pass review was performed)
                review_meta = metadata.get("review", {})
                was_reviewed = review_meta.get("reviewed", False)
                review_model = review_meta.get("review_model") if was_reviewed else None
                review_tokens = review_meta.get("review_tokens", 0) if was_reviewed else None
                review_cost = review_meta.get("review_cost", 0.0) if was_reviewed else None
                total_cost = generation_cost + (review_cost or 0.0)

                # For backward compatibility, use generation_cost as 'cost' for logging
                cost = total_cost

                # Log generation details
                review_info = f" + {review_model} review (${review_cost:.4f})" if was_reviewed else ""
                self._log_progress(
                    job,
                    f"✓ Summary generated using {approach} approach: {output_tokens} tokens, "
                    f"${generation_cost:.4f}{review_info}, total=${total_cost:.4f}, {generation_time_ms}ms"
                )

                # Log discussion notes word count if available (single-call only)
                word_count = metadata.get("discussion_notes_word_count")
                if word_count:
                    logger.info(f"Discussion notes word count: {word_count}")

                # Log review status
                if was_reviewed:
                    self._log_progress(job, f"✓ Two-pass review applied: {review_model} ({review_tokens} tokens)")

                # Extract classification metadata (enterprise intelligence)
                classification_data = {}
                if hasattr(self.summarizer, 'extract_classification_metadata'):
                    self._log_progress(job, "Extracting classification metadata for enterprise intelligence...")
                    try:
                        classification_data = await loop.run_in_executor(
                            None,
                            lambda: self.summarizer.extract_classification_metadata(
                                transcript_segments=transcript.parsed_content,
                                meeting_metadata=meeting_metadata
                            )
                        )
                        self._log_progress(
                            job,
                            f"Classification extracted: type={classification_data.get('meeting_type')}, "
                            f"sentiment={classification_data.get('overall_sentiment')}"
                        )
                    except Exception as ce:
                        logger.warning(f"Classification extraction failed (non-fatal): {ce}")
                        classification_data = {}

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

                # Use requested_version (already determined at top of function)
                version = requested_version

                # Save enhanced summary to database
                summary = Summary(
                    meeting_id=meeting_id,
                    transcript_id=transcript.id,
                    summary_text=summary_text,
                    summary_html=summary_html,
                    model=model,
                    approach=approach,  # Track gemini_single_call vs haiku_fallback
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=total_tokens,
                    generation_time_ms=generation_time_ms,
                    # Cost tracking (NEW)
                    generation_cost=generation_cost,
                    review_model=review_model,
                    review_tokens=review_tokens,
                    review_cost=review_cost,
                    total_cost=total_cost,
                    was_reviewed=was_reviewed,
                    # Enhanced summary data (NEW)
                    action_items_json=action_items,
                    decisions_json=decisions,
                    topics_json=topics,
                    highlights_json=highlights,
                    mentions_json=mentions,
                    key_numbers_json=key_numbers,  # FIX: Add key_numbers_json
                    ai_answerable_questions_json=ai_answerable_questions,
                    topics_to_explore_json=topics_to_explore,
                    version=version,
                    custom_instructions=custom_instructions,
                    # Enterprise intelligence metadata (classification extraction)
                    meeting_type=classification_data.get("meeting_type"),
                    meeting_category=classification_data.get("meeting_category"),
                    seniority_level=classification_data.get("seniority_level"),
                    department_context=classification_data.get("department_context"),
                    is_onboarding=classification_data.get("is_onboarding", False),
                    is_coaching=classification_data.get("is_coaching", False),
                    is_sales_meeting=classification_data.get("is_sales_meeting", False),
                    is_support_call=classification_data.get("is_support_call", False),
                    overall_sentiment=classification_data.get("overall_sentiment"),
                    urgency_level=classification_data.get("urgency_level"),
                    consensus_level=classification_data.get("consensus_level"),
                    has_concerns=classification_data.get("has_concerns", False),
                    meeting_effectiveness=classification_data.get("meeting_effectiveness"),
                    communication_style=classification_data.get("communication_style"),
                    energy_level=classification_data.get("energy_level"),
                    action_item_count=classification_data.get("action_item_count"),
                    decision_count=classification_data.get("decision_count"),
                    open_question_count=classification_data.get("open_question_count"),
                    blocker_count=classification_data.get("blocker_count"),
                    follow_up_required=classification_data.get("follow_up_required", False),
                    topics_discussed=classification_data.get("topics_discussed"),
                    projects_mentioned=classification_data.get("projects_mentioned"),
                    products_mentioned=classification_data.get("products_mentioned"),
                    technologies_discussed=classification_data.get("technologies_discussed"),
                    people_mentioned=classification_data.get("people_mentioned"),
                    deadlines_mentioned=classification_data.get("deadlines_mentioned"),
                    financial_mentions=classification_data.get("financial_mentions"),
                    concerns_json=classification_data.get("concerns_json"),
                    blockers_json=classification_data.get("blockers_json"),
                    market_intelligence_json=classification_data.get("market_intelligence_json"),
                    training_content_json=classification_data.get("training_content_json"),
                    has_external_participants=classification_data.get("has_external_participants", False),
                    external_company_names=classification_data.get("external_company_names"),
                    client_names=classification_data.get("client_names"),
                    competitor_names=classification_data.get("competitor_names"),
                    has_financial_discussion=classification_data.get("has_financial_discussion", False),
                    has_deadline_pressure=classification_data.get("has_deadline_pressure", False),
                    has_escalation=classification_data.get("has_escalation", False),
                    has_customer_complaint=classification_data.get("has_customer_complaint", False),
                    has_technical_discussion=classification_data.get("has_technical_discussion", False),
                    is_confidential=classification_data.get("is_confidential", False)
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

                # Update meeting (query it in THIS session to avoid detached object bug)
                meeting_in_session = session.query(Meeting).filter_by(id=meeting_id).first()
                if meeting_in_session:
                    meeting_in_session.has_summary = True

                # NOTE: Distribution job is already created by job chain (enqueue_meeting_jobs)
                # Do NOT create another distribute job here - it causes duplicate emails

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
                    extraction_calls=metadata.get("extraction_calls", 5),
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
