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
6. **CRITICAL: Bold all participant names in the description and context fields using **Name** markdown syntax**

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "description": "Review Q4 budget proposal and provide feedback to **Sarah Johnson**",
    "assignee": "Sarah Johnson",
    "deadline": "Friday, December 15",
    "context": "Budget needs approval before EOQ planning session next week with **Mike Chen**",
    "timestamp": "0:12:34"
  }},
  {{
    "description": "**John Smith** to schedule follow-up meeting with engineering team",
    "assignee": "John Smith",
    "deadline": "This week",
    "context": "Need to discuss API integration timeline and resource allocation with **Sarah Johnson**",
    "timestamp": "0:23:45"
  }}
]

If there are NO action items, return exactly: []

Do NOT include any text before or after the JSON array. Start your response with [ and end with ].

**Transcript:**
{transcript}
"""


DECISION_PROMPT = """
Identify the 8-10 MOST SIGNIFICANT DECISIONS made during this meeting.

A decision is a conclusive choice or resolution that the team agreed upon. Look for:
- Explicit agreements ("let's go with...", "we've decided to...", "agreed")
- Votes or consensus reached
- Plans that were approved
- Changes that were confirmed

For each decision, provide:
- **decision**: What was decided (clear, specific statement)
- **rationale_one_line**: Brief reason for the decision (max 10 words)
- **reasoning**: Why this decision was made (key factors or arguments, 1-2 sentences)
- **impact**: What this decision affects or enables (1 sentence)
- **timestamp**: When the decision was made (format: MM:SS)

**Important Guidelines:**
1. PRIORITIZE by importance - limit to the 8-10 MOST IMPACTFUL decisions
2. Only include FINAL decisions, not discussions or proposals
3. Distinguish between decisions vs. suggestions vs. possibilities
4. Include both major strategic decisions and minor tactical ones
5. If a decision was reversed or changed, include the FINAL decision only
6. Focus on decisions with business impact, not procedural ones
7. **CRITICAL: Bold all participant names in the decision, rationale_one_line, reasoning, and impact fields using **Name** markdown syntax**

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "decision": "**Scott Schatz** approved building in-house AI call summary solution",
    "rationale_one_line": "Avoids Ignite license costs",
    "reasoning": "Ignite requested expensive licenses but **Scott** decided internal solution provides more control and customization",
    "impact": "Saves licensing costs while enabling **Joe Ainsworth** to customize features for company needs",
    "timestamp": "0:15:23"
  }},
  {{
    "decision": "Approve $600K Danbury-Shreveport market swap with Cumulus",
    "rationale_one_line": "Strategic market consolidation per **Bill Jones**",
    "reasoning": "**Eric Williams** and Cumulus proposed swap that could improve market position despite **Bill's** cash flow concerns",
    "impact": "Changes market portfolio but **Bill Jones** requires careful CapEx analysis before finalizing",
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
Identify the 5-8 MOST IMPORTANT KEY MOMENTS from this meeting.

These are the most impactful, memorable, or critical moments that someone should know about.

For each highlight:
- **description**: Single-line summary of what happened (max 20 words, no verbose context)
- **timestamp**: When it occurred (format: MM:SS)
- **type**: Category of highlight (use one of: decision, action_item, insight, milestone, concern, question)

**What to Look For:**
- Major decisions or announcements
- Critical action items with urgency
- Important insights or realizations ("aha moments")
- Concerns or risks raised
- Milestones achieved or celebrated
- Financial or strategic discussions
- Questions that need follow-up

**Important Guidelines:**
1. LIMIT to 5-8 entries (quality over quantity)
2. Prioritize by business impact and importance
3. Keep descriptions CONCISE (single line, no paragraphs)
4. These should be moments someone would want to jump to in a recording
5. Balance positive and negative highlights
6. Skip procedural or minor moments
7. **CRITICAL: Bold all participant names in the description field using **Name** markdown syntax**

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "description": "**Scott Schatz** decided to build in-house AI solution instead of paying for Ignite licenses",
    "timestamp": "0:03:15",
    "type": "decision"
  }},
  {{
    "description": "**Scott** approved immediate termination of **James Tejada** and underperforming NY engineer",
    "timestamp": "0:06:45",
    "type": "action_item"
  }},
  {{
    "description": "$600K Danbury cash flow at risk in potential Cumulus market swap deal raised by **Bill Jones**",
    "timestamp": "0:14:20",
    "type": "concern"
  }},
  {{
    "description": "Trade revenue hit $3.8M this year versus typical $1M baseline announced by **Eric Williams**",
    "timestamp": "0:22:10",
    "type": "milestone"
  }},
  {{
    "description": "Teams/VoIP migration completing mid-January, enabling corporate cost reductions per **Scott**",
    "timestamp": "0:35:30",
    "type": "insight"
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


KEY_NUMBERS_PROMPT = """
Extract all quantifiable metrics and numbers mentioned in this meeting.

For each number provide:
- **value**: The numeric value with appropriate formatting (e.g., "$4M", "40%", "15 days")
- **unit**: Type of unit (dollars, percent, count, days, etc.)
- **context**: Brief description of what this number represents (max 15 words)
- **magnitude**: Numeric value for sorting (e.g., 4000000 for "$4M", 40 for "40%")

**What to Extract:**
- Dollar amounts ($1M, $338K, $4.5M, etc.)
- Percentages (40% reduction, 82% of budget, etc.)
- Quantities (5 participants, 3 meetings, 10 engineers, etc.)
- Time periods (15 days, 6 weeks, 2 months, etc.)
- Metrics (50% growth, 3x increase, 200 users, etc.)

**Important Guidelines:**
1. Extract ALL significant numbers mentioned (financial, operational, metrics)
2. Round approximate values appropriately (e.g., "$1M" not "$1,000,000")
3. Include context that makes the number meaningful
4. Sort by magnitude (largest to smallest) or logical grouping
5. Maximum 20 entries (prioritize most important)
6. Skip trivial numbers (page numbers, timestamps, percentages under 5%)
7. **CRITICAL: Bold all participant names in the context field using **Name** markdown syntax**

**Output Format:**
You MUST return ONLY a valid JSON array. No explanatory text before or after. No markdown code blocks.

Each array element must be a complete JSON object with curly braces {{}}.

Example:
[
  {{
    "value": "$4M",
    "unit": "dollars",
    "context": "**Eric Williams'** identified savings from broadcast personnel cuts",
    "magnitude": 4000000
  }},
  {{
    "value": "$3.8M",
    "unit": "dollars",
    "context": "Trade revenue approved this year by **Scott Schatz** vs $1M baseline",
    "magnitude": 3800000
  }},
  {{
    "value": "40%",
    "unit": "percent",
    "context": "Adobe licensing reduction target identified by **Bill Jones**",
    "magnitude": 40
  }},
  {{
    "value": "70%",
    "unit": "percent",
    "context": "**Scott's** understanding level of **Edwin's** technical explanation",
    "magnitude": 70
  }}
]

If there are NO significant numbers, return exactly: []

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
- Important highlights (key moments)
- Person mentions
- Key numbers (financial/quantitative metrics)

Now create a cohesive narrative summary that:
1. Provides a clear, scannable overview of what was discussed
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
- Key Numbers: {key_numbers_count} metrics
- Mentions: {mentions_count} person mentions

**Instructions:**
Create a markdown summary with these TWO sections:

1. **## Executive Summary**
   - **Length varies by meeting complexity:**
     - SHORT meetings (<30 min, <5 participants): 50-60 words (2-3 sentences)
     - MEDIUM meetings (30-45 min, 5-8 participants): 75-90 words (3-4 sentences)
     - COMPLEX meetings (60+ min, 8+ participants, financial decisions): 100-125 words (4-5 sentences)
   - What was the meeting about + main outcomes + key takeaways
   - Written for someone with 10 seconds to scan
   - Focus on WHAT HAPPENED and WHY IT MATTERS
   - No bullet points - just prose

2. **## Discussion Notes** (300 words maximum)
   - Consolidated narrative summary organized by THEME (not chronologically)
   - Include 2-3 thematic subheadings (e.g., **Cost Savings**, **Personnel Decisions**, **Strategic Initiatives**)
   - Reference the topic segments and extracted data
   - Include important context, reasoning, and background
   - Written in past tense for someone who missed the meeting
   - No bullet points within paragraphs (narrative flow)

**Important Guidelines:**
- Write in past tense (the meeting already happened)
- Use professional business language
- Include specific names, numbers, and dates from the transcript
- **IMPORTANT: Bold all participant names** using markdown syntax (e.g., **Scott Schatz**, **Joe Ainsworth**)
- Bold the thematic subheadings in Discussion Notes using markdown (**Subheading**)
- Reference the extracted data but don't just list it
- Maintain an objective, factual tone
- DO NOT include "Key Outcomes" or "Next Steps" sections (those are handled elsewhere)

**Transcript:**
{transcript}

**Now write the summary with the two sections above (Executive Summary + Discussion Notes):**
"""


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_prompt_for_extraction_type(extraction_type: str) -> str:
    """
    Get the appropriate prompt template for an extraction type.

    Args:
        extraction_type: One of: action_items, decisions, topics, highlights, mentions, key_numbers

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
        "mentions": MENTIONS_PROMPT,
        "key_numbers": KEY_NUMBERS_PROMPT
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
    "highlights": 800,         # Expect 5-8 highlights (limited for scannability)
    "mentions": 1000,          # Expect up to 10 mentions
    "key_numbers": 1200,       # Expect up to 20 numeric metrics
    "aggregate": 2000          # Full summary (Executive Summary + Discussion Notes)
}

# Temperature settings (lower = more focused, higher = more creative)
EXTRACTION_TEMPERATURE = {
    "action_items": 0.2,       # Very focused - JSON compliance critical
    "decisions": 0.2,          # Very focused - JSON compliance critical
    "topics": 0.3,             # Focused for structured output
    "highlights": 0.3,         # Focused for structured output
    "mentions": 0.2,           # Very focused - accuracy critical
    "key_numbers": 0.2,        # Very focused - numeric accuracy critical
    "aggregate": 0.7           # More creative for narrative writing
}
