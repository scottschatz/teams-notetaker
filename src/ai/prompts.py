"""
Prompt templates for Claude AI summarization.

Contains system prompts and template functions for generating
meeting summaries from transcripts.
"""

from typing import List, Dict

# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

SUMMARY_SYSTEM_PROMPT = """You are an expert meeting summarizer for a professional organization. Your task is to create clear, concise, and actionable meeting summaries from Microsoft Teams meeting transcripts.

Guidelines:
- Focus on key decisions, action items, and important discussions
- Use bullet points for readability
- Identify speakers when important for context
- Highlight deadlines and commitments
- Keep summaries under 500 words unless the meeting is particularly complex
- Use professional but conversational tone
- If the transcript is unclear or low quality, note that in the summary
- Do not invent information not present in the transcript
- For action items, try to identify the person responsible if mentioned
- Format decisions clearly so they stand out
- Include relevant context for follow-up

Output Format:
Your summary should have these sections:
1. Executive Summary (2-3 sentences)
2. Key Discussion Points (bullet points)
3. Decisions Made (bullet points, or "None recorded" if no decisions)
4. Action Items (with owners if identifiable, or "None recorded" if no action items)
5. Next Steps (if mentioned)"""


# ============================================================================
# PROMPT TEMPLATES
# ============================================================================


def build_summary_prompt(transcript_segments: List[Dict[str, str]], meeting_metadata: Dict) -> str:
    """
    Build prompt for Claude from parsed transcript and meeting metadata.

    Args:
        transcript_segments: Parsed transcript from VTT parser
            [{"speaker": "John", "text": "...", "timestamp": "00:01:30"}, ...]
        meeting_metadata: Meeting information
            {
                "subject": "Weekly Team Sync",
                "organizer": "sarah@example.com",
                "start_time": "2025-12-10 14:00:00",
                "duration_minutes": 30,
                "participant_count": 4,
                "participants": ["Sarah", "John", "Mike", "Lisa"]
            }

    Returns:
        Formatted prompt string for Claude API
    """
    # Format transcript with timestamps
    transcript_lines = []
    for segment in transcript_segments:
        timestamp = segment.get("timestamp", "00:00:00").split(".")[0]  # Remove milliseconds
        speaker = segment.get("speaker", "Unknown")
        text = segment.get("text", "")
        transcript_lines.append(f"[{timestamp}] {speaker}: {text}")

    transcript_text = "\n".join(transcript_lines)

    # Format participants list
    participants = meeting_metadata.get("participants", [])
    if participants:
        participants_str = ", ".join(participants)
    else:
        participants_str = f"{meeting_metadata.get('participant_count', 'Unknown')} participants"

    # Build the prompt
    prompt = f"""Please summarize the following Microsoft Teams meeting:

**Meeting Information:**
- Subject: {meeting_metadata.get('subject', 'Unknown')}
- Organizer: {meeting_metadata.get('organizer', 'Unknown')}
- Date/Time: {meeting_metadata.get('start_time', 'Unknown')}
- Duration: {meeting_metadata.get('duration_minutes', 'Unknown')} minutes
- Participants: {participants_str}

**Transcript:**
{transcript_text}

Please provide a comprehensive summary following the format specified in your instructions."""

    return prompt


def build_action_items_extraction_prompt(transcript_text: str) -> str:
    """
    Build prompt specifically for extracting action items from transcript.

    This can be used as a follow-up prompt to get more detailed action items.

    Args:
        transcript_text: Formatted transcript text

    Returns:
        Prompt for action item extraction
    """
    prompt = f"""Review the following meeting transcript and extract ALL action items, tasks, or commitments mentioned.

For each action item, provide:
1. Description of the task
2. Person responsible (if mentioned)
3. Deadline or timeframe (if mentioned)
4. Any dependencies or blockers

**Transcript:**
{transcript_text}

Format your response as a numbered list. If no action items were mentioned, respond with "No action items identified in this meeting."
"""

    return prompt


def build_decision_extraction_prompt(transcript_text: str) -> str:
    """
    Build prompt specifically for extracting decisions from transcript.

    Args:
        transcript_text: Formatted transcript text

    Returns:
        Prompt for decision extraction
    """
    prompt = f"""Review the following meeting transcript and identify ALL decisions that were made.

For each decision, provide:
1. What was decided
2. Who made or approved the decision (if clear)
3. Context or rationale (if mentioned)
4. Any next steps resulting from the decision

**Transcript:**
{transcript_text}

Format your response as a numbered list. If no clear decisions were made, respond with "No formal decisions recorded in this meeting."
"""

    return prompt


def build_topic_based_summary_prompt(transcript_segments: List[Dict[str, str]], topics: List[str]) -> str:
    """
    Build prompt for topic-based summary (useful for long meetings).

    Args:
        transcript_segments: Parsed transcript segments
        topics: List of topics to focus on

    Returns:
        Prompt for topic-based summarization
    """
    # Format transcript
    transcript_lines = []
    for segment in transcript_segments:
        speaker = segment.get("speaker", "Unknown")
        text = segment.get("text", "")
        transcript_lines.append(f"{speaker}: {text}")

    transcript_text = "\n".join(transcript_lines)

    # Format topics
    topics_str = "\n".join([f"- {topic}" for topic in topics])

    prompt = f"""Please summarize the following meeting transcript, focusing on these specific topics:

{topics_str}

For each topic, provide:
1. What was discussed
2. Any decisions made
3. Action items related to that topic

**Transcript:**
{transcript_text}

If a topic was not discussed in the meeting, note that explicitly.
"""

    return prompt


# ============================================================================
# CUSTOM PROMPT TEMPLATES (for specific use cases)
# ============================================================================


def build_technical_meeting_prompt(transcript_segments: List[Dict[str, str]], meeting_metadata: Dict) -> str:
    """
    Build prompt optimized for technical/engineering meetings.

    Focuses on technical decisions, blockers, and implementation details.

    Args:
        transcript_segments: Parsed transcript segments
        meeting_metadata: Meeting information

    Returns:
        Prompt optimized for technical meetings
    """
    # Format transcript
    transcript_lines = []
    for segment in transcript_segments:
        timestamp = segment.get("timestamp", "00:00:00").split(".")[0]
        speaker = segment.get("speaker", "Unknown")
        text = segment.get("text", "")
        transcript_lines.append(f"[{timestamp}] {speaker}: {text}")

    transcript_text = "\n".join(transcript_lines)

    prompt = f"""Summarize this technical/engineering meeting with a focus on:
- Technical decisions and architecture choices
- Implementation approaches and strategies
- Blockers and challenges discussed
- Code reviews or technical debt items
- Infrastructure or deployment plans
- Testing and quality assurance discussions

**Meeting:** {meeting_metadata.get('subject', 'Technical Meeting')}
**Date:** {meeting_metadata.get('start_time', 'Unknown')}

**Transcript:**
{transcript_text}

Provide a summary that helps engineering teams understand what was decided and what needs to be done.
"""

    return prompt


def build_executive_brief_prompt(transcript_segments: List[Dict[str, str]], meeting_metadata: Dict) -> str:
    """
    Build prompt for executive brief (very short summary).

    Args:
        transcript_segments: Parsed transcript segments
        meeting_metadata: Meeting information

    Returns:
        Prompt for executive brief
    """
    # Format transcript (no timestamps for executive brief)
    transcript_lines = []
    for segment in transcript_segments:
        speaker = segment.get("speaker", "Unknown")
        text = segment.get("text", "")
        transcript_lines.append(f"{speaker}: {text}")

    transcript_text = "\n".join(transcript_lines)

    prompt = f"""Create an executive brief (3-5 sentences maximum) for the following meeting.

Focus ONLY on:
1. The single most important outcome or decision
2. Critical action items requiring executive attention
3. Major blockers or risks

**Meeting:** {meeting_metadata.get('subject', 'Meeting')}

**Transcript:**
{transcript_text}

Be extremely concise. Executives should be able to read this in under 30 seconds.
"""

    return prompt


# ============================================================================
# PROMPT VALIDATION
# ============================================================================


def estimate_token_count(text: str) -> int:
    """
    Estimate token count for Claude API.

    Uses rough heuristic: ~4 characters per token.

    Args:
        text: Text to estimate

    Returns:
        Estimated token count
    """
    return len(text) // 4


def validate_prompt_length(prompt: str, max_tokens: int = 100000) -> tuple[bool, int]:
    """
    Validate that prompt is not too long for Claude API.

    Claude 3.5 Sonnet has 200k token context window.

    Args:
        prompt: Prompt to validate
        max_tokens: Maximum tokens allowed (default: 100k for input)

    Returns:
        (is_valid, estimated_tokens)
    """
    estimated_tokens = estimate_token_count(prompt)
    is_valid = estimated_tokens <= max_tokens

    return is_valid, estimated_tokens


def truncate_transcript_if_needed(
    transcript_segments: List[Dict[str, str]], max_tokens: int = 50000
) -> List[Dict[str, str]]:
    """
    Truncate transcript segments if they would exceed token limit.

    Keeps beginning and end of transcript, truncating middle if needed.

    Args:
        transcript_segments: Parsed transcript segments
        max_tokens: Maximum tokens to use for transcript

    Returns:
        Potentially truncated list of segments
    """
    # Estimate current token count
    total_text = " ".join([seg.get("text", "") for seg in transcript_segments])
    current_tokens = estimate_token_count(total_text)

    if current_tokens <= max_tokens:
        return transcript_segments  # No truncation needed

    # Need to truncate - keep first 40% and last 40%, skip middle 20%
    keep_ratio = 0.4
    total_segments = len(transcript_segments)
    keep_count = int(total_segments * keep_ratio)

    truncated = transcript_segments[:keep_count] + transcript_segments[-keep_count:]

    return truncated
