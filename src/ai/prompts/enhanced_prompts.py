"""
Enhanced AI Prompt Templates

Multi-stage prompt system for extracting structured data from meeting transcripts.
Inspired by VTTMeetingNoteGenerator's tiered approach and Teams Copilot features.

Usage:
    from src.ai.prompts.enhanced_prompts import ACTION_ITEM_PROMPT, DECISION_PROMPT

    result = claude_client.complete(
        prompt=ACTION_ITEM_PROMPT.format(transcript=transcript_text),
        model="claude-sonnet-4.5"
    )
"""

# ============================================================================
# STAGE 1: EXTRACTION PROMPTS
# ============================================================================

ACTION_ITEM_PROMPT = """
Analyze this meeting transcript and extract ALL action items, tasks, and to-dos.

For each action item, provide:
- **description**: Clear, actionable task description (what needs to be done)
- **assignee**: Person responsible (full name if mentioned, or "Unassigned" if unclear)
- **deadline**: Due date or timeframe (if mentioned, or "Not specified")
- **context**: Why this task is needed (1-2 sentences of background)
- **timestamp**: When it was mentioned in the meeting (format: H:MM:SS)

**Important Guidelines:**
1. Only include EXPLICIT action items (not general discussions)
2. Look for phrases like: "can you...", "please...", "we need to...", "I'll...", "[name] will..."
3. If multiple people discuss the same task, choose the primary assignee
4. Include both immediate tasks and follow-up items
5. Do NOT include hypothetical or conditional tasks ("if we decide to...")

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "description": "Review Q4 budget proposal and provide feedback",
    "assignee": "Sarah Johnson",
    "deadline": "Friday, December 15",
    "context": "Budget needs approval before EOQ planning session next week",
    "timestamp": "0:12:34"
  }},
  {{
    "description": "Schedule follow-up meeting with engineering team",
    "assignee": "John Smith",
    "deadline": "This week",
    "context": "Need to discuss API integration timeline and resource allocation",
    "timestamp": "0:23:45"
  }}
]

If there are NO action items, return exactly: []

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


DECISION_PROMPT = """
Identify all significant DECISIONS made during this meeting.

A decision is a conclusive choice or resolution that the team agreed upon. Look for:
- Explicit agreements ("let's go with...", "we've decided to...", "agreed")
- Votes or consensus reached
- Plans that were approved
- Changes that were confirmed

For each decision, provide:
- **decision**: What was decided (clear, specific statement)
- **participants**: Who was involved in making the decision (names)
- **reasoning**: Why this decision was made (key factors or arguments)
- **impact**: What this decision affects or enables
- **timestamp**: When the decision was made (format: MM:SS)

**Important Guidelines:**
1. Only include FINAL decisions, not discussions or proposals
2. Distinguish between decisions vs. suggestions vs. possibilities
3. Include both major strategic decisions and minor tactical ones
4. If a decision was reversed or changed, include the FINAL decision only

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "decision": "Migrate to microservices architecture for user authentication service",
    "participants": "Sarah Johnson, Mike Chen, Development Team",
    "reasoning": "Current monolith is causing deployment bottlenecks and the auth service is independently scalable",
    "impact": "Will require 6-week refactor but enables faster feature releases and better fault isolation",
    "timestamp": "0:15:23"
  }},
  {{
    "decision": "Weekly standup meetings will move to Tuesdays at 10am",
    "participants": "Team consensus",
    "reasoning": "Monday mornings conflict with sprint planning, Tuesday works better for everyone",
    "impact": "Improved attendance and more productive discussions",
    "timestamp": "0:42:10"
  }}
]

If there are NO decisions, return exactly: []

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


TOPIC_SEGMENTATION_PROMPT = """
Break this meeting into 3-5 main discussion topics.

For each topic provide ONLY:
- **topic**: Brief topic name (3-5 words max)
- **duration**: Start-end time (format: "MM:SS - MM:SS")
- **summary**: One sentence summary (max 20 words)

Keep responses SHORT and focused. Limit to 5 topics maximum.

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example (SHORT format):
[
  {{
    "topic": "Q4 Project Status",
    "duration": "00:00 - 08:30",
    "summary": "Reviewed deliverables, API integration behind schedule"
  }},
  {{
    "topic": "Budget Planning",
    "duration": "08:31 - 18:45",
    "summary": "Approved hiring and infrastructure spending"
  }}
]

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


HIGHLIGHTS_PROMPT = """
Identify 3-5 KEY MOMENTS from this meeting that should be highlighted.

These are the most important, impactful, or memorable moments that someone should know about.

For each highlight:
- **title**: Brief descriptive title (5-10 words)
- **timestamp**: When it occurred (format: MM:SS)
- **why_important**: Why this moment matters (1-2 sentences)
- **type**: Category of highlight (use one of: decision, action_item, insight, milestone, concern, question)

**What to Look For:**
- Major decisions or announcements
- Critical action items with urgency
- Important insights or realizations ("aha moments")
- Concerns or risks raised
- Milestones achieved or celebrated
- Questions that need follow-up

**Important Guidelines:**
1. Select the MOST impactful moments (aim for 3-5, not more than 7)
2. These should be moments someone would want to jump to in a recording
3. Prioritize items with business impact
4. Balance positive and negative highlights

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "title": "Critical Security Vulnerability Discovered in Payment System",
    "timestamp": "0:12:45",
    "why_important": "Requires immediate attention to prevent potential data breach. All hands meeting scheduled for tomorrow.",
    "type": "concern"
  }},
  {{
    "title": "Q4 Revenue Target Exceeded by 23%",
    "timestamp": "0:03:15",
    "why_important": "Company achieved record quarterly revenue, enabling increased investment in product development.",
    "type": "milestone"
  }},
  {{
    "title": "Decision to Acquire CompetitorCo Announced",
    "timestamp": "0:28:30",
    "why_important": "Strategic acquisition will expand market share by 40% and add 50 enterprise customers.",
    "type": "decision"
  }}
]

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


MENTIONS_PROMPT = """
Identify up to 10 key mentions of specific people in this meeting.

For each mention provide ONLY:
- **person**: Person's name
- **mentioned_by**: Who mentioned them
- **context**: What was said (max 15 words)
- **timestamp**: When (MM:SS)
- **type**: One of: question, action_assignment, recognition, other

Focus on the MOST IMPORTANT mentions only. Limit to 10 mentions maximum. Keep context brief.

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "person": "Sarah Johnson",
    "mentioned_by": "John Smith",
    "context": "Asked to review the Q4 budget proposal by Friday and provide feedback on the infrastructure spending section.",
    "timestamp": "0:12:34",
    "type": "action_assignment"
  }},
  {{
    "person": "Mike Chen",
    "mentioned_by": "Sarah Johnson",
    "context": "Recognized for exceptional work on the mobile app launch, which came in ahead of schedule and under budget.",
    "timestamp": "0:05:20",
    "type": "recognition"
  }},
  {{
    "person": "Alex Rodriguez",
    "mentioned_by": "John Smith",
    "context": "Asked about the timeline for completing the API integration and whether additional resources are needed.",
    "timestamp": "0:15:45",
    "type": "question"
  }}
]

If there are NO mentions, return exactly: []

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


# ============================================================================
# STAGE 2: AGGREGATION PROMPT
# ============================================================================

AGGREGATE_SUMMARY_PROMPT = """
You are an expert meeting summarizer. Create a comprehensive, well-structured meeting summary.

You have already extracted structured data from the meeting:
- Action items with assignees and deadlines
- Key decisions with reasoning
- Topic segments with summaries
- Important highlights
- Person mentions

Now create a cohesive narrative summary that:
1. Provides a clear overview of what was discussed
2. Highlights the most important outcomes
3. Maintains a professional, objective tone
4. Includes specific details (numbers, names, dates)
5. Is organized with clear sections using markdown

**Meeting Metadata:**
{metadata}

**Extracted Data:**
- Action Items: {action_items_count} items
- Decisions: {decisions_count} decisions
- Topics: {topics_count} topics discussed
- Highlights: {highlights_count} key moments
- Mentions: {mentions_count} person mentions

**Instructions:**
Create a markdown summary with these sections:

1. **## Executive Summary** (2-3 paragraphs)
   - What was the meeting about?
   - What were the main outcomes?
   - What are the next steps?

2. **## Key Outcomes**
   - List the most important results (decisions + critical action items)
   - Use bullet points, be specific

3. **## Discussion Overview**
   - Narrative summary of the main discussions
   - Reference the topic segments
   - Include important context and reasoning

4. **## Next Steps**
   - Forward-looking action items
   - Dependencies or blockers
   - Follow-up meetings needed

**Important Guidelines:**
- Write in past tense (the meeting already happened)
- Be concise but specific (aim for 400-600 words total)
- Use professional business language
- Include specific names, numbers, and dates from the transcript
- **IMPORTANT: Bold all participant names** using markdown syntax (e.g., **Scott Schatz**, **Joe Ainsworth**)
- Reference the extracted data but don't just list it
- Maintain an objective, factual tone

**Transcript:**
{transcript}

**Now write the summary:**
"""


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_prompt_for_extraction_type(extraction_type: str) -> str:
    """
    Get the appropriate prompt template for an extraction type.

    Args:
        extraction_type: One of: action_items, decisions, topics, highlights, mentions

    Returns:
        Prompt template string

    Raises:
        ValueError: If extraction_type is not recognized
    """
    prompts = {
        "action_items": ACTION_ITEM_PROMPT,
        "decisions": DECISION_PROMPT,
        "topics": TOPIC_SEGMENTATION_PROMPT,
        "highlights": HIGHLIGHTS_PROMPT,
        "mentions": MENTIONS_PROMPT
    }

    if extraction_type not in prompts:
        raise ValueError(
            f"Unknown extraction type: {extraction_type}. "
            f"Must be one of: {', '.join(prompts.keys())}"
        )

    return prompts[extraction_type]


def format_transcript_for_extraction(segments: list) -> str:
    """
    Format parsed VTT segments for extraction prompts.

    Args:
        segments: List of parsed VTT segments with speaker, text, timestamp

    Returns:
        Formatted transcript string with timestamps and speakers
    """
    lines = []

    for segment in segments:
        speaker = segment.get("speaker", "Unknown")
        text = segment.get("text", "")
        start_seconds = segment.get("start_seconds", 0)  # VTT parser uses 'start_seconds'

        # Convert seconds to H:MM:SS format (matching transcript display)
        hours = int(start_seconds / 3600)
        minutes = int((start_seconds % 3600) / 60)
        seconds = int(start_seconds % 60)
        timestamp = f"{hours}:{minutes:02d}:{seconds:02d}"

        # Format: [H:MM:SS] Speaker: Text
        lines.append(f"[{timestamp}] {speaker}: {text}")

    return "\n".join(lines)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Token limits for different extraction types
EXTRACTION_TOKEN_LIMITS = {
    "action_items": 1500,      # Expect 5-20 action items (increased to prevent truncation)
    "decisions": 1500,         # Expect 3-10 decisions (increased to prevent truncation)
    "topics": 800,             # Expect 3-5 topics with short summaries
    "highlights": 800,         # Expect 3-5 highlights
    "mentions": 1000,          # Expect up to 10 mentions
    "aggregate": 2000          # Full summary
}

# Temperature settings (lower = more focused, higher = more creative)
EXTRACTION_TEMPERATURE = {
    "action_items": 0.2,       # Very focused - JSON compliance critical
    "decisions": 0.2,          # Very focused - JSON compliance critical
    "topics": 0.3,             # Focused for structured output
    "highlights": 0.3,         # Focused for structured output
    "mentions": 0.2,           # Very focused - accuracy critical
    "aggregate": 0.7           # More creative for narrative writing
}
