"""
Single-call comprehensive meeting summarization prompt.

Combines all extraction stages into a single API call for improved
efficiency, cost savings, and quality.
"""

SINGLE_CALL_COMPREHENSIVE_PROMPT = """Analyze this meeting transcript and extract ALL structured information in a single JSON response.

You MUST return ONLY a valid JSON object. No explanatory text before or after. No markdown code blocks. Start with {{ and end with }}.

**REQUIRED OUTPUT STRUCTURE:**

{{
  "action_items": [...],      // Array of action item objects
  "decisions": [...],          // Array of decision objects (8-10 max)
  "highlights": [...],         // Array of key moment objects (5-8 max)
  "key_numbers": [...],        // Array of quantitative metric objects (max 20)
  "executive_summary": "...",  // String (50-125 words, varies by meeting complexity)
  "discussion_notes": "..."    // String (appropriate length based on meeting complexity)
}}

---

**ACTION ITEMS EXTRACTION:**

Extract ALL action items, tasks, and to-dos. For each provide:
- **description**: Clear, actionable task description (what needs to be done)
- **assignee**: Person responsible (full name if mentioned, or "Unassigned" if unclear)
- **deadline**: Due date or timeframe (if mentioned, or "Not specified")
- **context**: Why this task is needed (1-2 sentences of background)
- **timestamp**: When it was mentioned in the meeting (format: H:MM:SS)

Guidelines:
- Only include EXPLICIT action items (not general discussions)
- Look for phrases like: "can you...", "please...", "we need to...", "I'll...", "[name] will..."
- If multiple people discuss the same task, choose the primary assignee
- Include both immediate tasks and follow-up items
- Do NOT include hypothetical or conditional tasks ("if we decide to...")
- **CRITICAL: Bold all participant names using **Name** markdown syntax**
- **CRITICAL: Verify assignee attribution by checking the <v SpeakerName> tags - only assign to people who explicitly accepted the task**

Example action_items entry:
{{
  "description": "Review Q4 budget proposal and provide feedback to **Sarah Johnson**",
  "assignee": "Sarah Johnson",
  "deadline": "Friday, December 15",
  "context": "Budget needs approval before EOQ planning session next week with **Mike Chen**",
  "timestamp": "0:12:34"
}}

If no action items, use: "action_items": []

---

**DECISIONS EXTRACTION:**

Identify the 8-10 MOST SIGNIFICANT DECISIONS made during this meeting. For each provide:
- **decision**: What was decided (clear, specific statement)
- **rationale_one_line**: Brief reason for the decision (max 10 words)
- **reasoning**: Why this decision was made (key factors or arguments, 1-2 sentences)
- **impact**: What this decision affects or enables (1 sentence)
- **timestamp**: When the decision was made (format: MM:SS)

Guidelines:
- PRIORITIZE by importance - limit to the 8-10 MOST IMPACTFUL decisions
- Only include FINAL decisions, not discussions or proposals
- Distinguish between decisions vs. suggestions vs. possibilities
- Include both major strategic decisions and minor tactical ones
- If a decision was reversed or changed, include the FINAL decision only
- Focus on decisions with business impact, not procedural ones
- **CRITICAL: Bold all participant names using **Name** markdown syntax**
- **CRITICAL: Verify who made each decision by checking the <v SpeakerName> tags - attribute decisions to the person who actually stated or approved them**

Example decisions entry:
{{
  "decision": "**Scott Schatz** approved building in-house AI call summary solution",
  "rationale_one_line": "Avoids Ignite license costs",
  "reasoning": "Ignite requested expensive licenses but **Scott** decided internal solution provides more control and customization",
  "impact": "Saves licensing costs while enabling **Joe Ainsworth** to customize features for company needs",
  "timestamp": "0:15:23"
}}

If no decisions, use: "decisions": []

---

**KEY MOMENTS (HIGHLIGHTS) EXTRACTION:**

Identify the 5-8 MOST IMPORTANT KEY MOMENTS from this meeting. For each provide:
- **description**: Single-line summary of what happened (max 20 words, no verbose context)
- **timestamp**: When it occurred (format: MM:SS)
- **type**: Category of highlight (use one of: decision, action_item, insight, milestone, concern, question)

Guidelines:
- LIMIT to 5-8 entries (quality over quantity)
- Prioritize by business impact and importance
- Keep descriptions CONCISE (single line, no paragraphs)
- These should be moments someone would want to jump to in a recording
- Balance positive and negative highlights
- Skip procedural or minor moments
- **CRITICAL: Bold all participant names using **Name** markdown syntax**
- **CRITICAL: Verify speaker attribution is ACCURATE - check the <v SpeakerName> tags in the transcript to confirm who actually said something before attributing it to them**

Example highlights entry:
{{
  "description": "**Scott Schatz** decided to build in-house AI solution instead of paying for Ignite licenses",
  "timestamp": "0:03:15",
  "type": "decision"
}}

If no highlights, use: "highlights": []

---

**KEY NUMBERS EXTRACTION:**

Extract all quantifiable metrics and numbers mentioned in this meeting. For each provide:
- **value**: The numeric value with appropriate formatting (e.g., "$4M", "40%", "15 days")
- **unit**: Type of unit (dollars, percent, count, days, etc.)
- **context**: Brief description of what this number represents (max 15 words)
- **magnitude**: Numeric value for sorting (e.g., 4000000 for "$4M", 40 for "40%")

What to extract:
- Dollar amounts ($1M, $338K, $4.5M, etc.)
- Percentages (40% reduction, 82% of budget, etc.)
- Quantities (5 participants, 3 meetings, 10 engineers, etc.)
- Time periods (15 days, 6 weeks, 2 months, etc.)
- Metrics (50% growth, 3x increase, 200 users, etc.)

Guidelines:
- Extract ALL significant numbers mentioned (financial, operational, metrics)
- Round approximate values appropriately (e.g., "$1M" not "$1,000,000")
- Include context that makes the number meaningful
- Sort by magnitude (largest to smallest) or logical grouping
- Maximum 20 entries (prioritize most important)
- Skip trivial numbers (page numbers, timestamps, percentages under 5%)
- **CRITICAL: Bold all participant names using **Name** markdown syntax**

Example key_numbers entry:
{{
  "value": "$4M",
  "unit": "dollars",
  "context": "**Eric Williams'** identified savings from broadcast personnel cuts",
  "magnitude": 4000000
}}

If no key numbers, use: "key_numbers": []

---

**EXECUTIVE SUMMARY GENERATION:**

Create a concise prose summary that captures the essence of the meeting.

Length varies by meeting complexity:
- SHORT meetings (<30 min, <5 participants): 50-60 words (2-3 sentences)
- MEDIUM meetings (30-45 min, 5-8 participants): 75-90 words (3-4 sentences)
- COMPLEX meetings (60+ min, 8+ participants, financial decisions): 100-125 words (4-5 sentences)

Content:
- What was the meeting about + main outcomes + key takeaways
- Written for someone with 10 seconds to scan
- Focus on WHAT HAPPENED and WHY IT MATTERS
- No bullet points - just prose
- Write in past tense (the meeting already happened)
- Use professional business language
- Include specific names, numbers, and dates from the transcript
- **IMPORTANT: Bold all participant names** using **Name** markdown

Example executive_summary:
"**Scott Schatz** led a strategic meeting addressing AI technology decisions, personnel changes, and market opportunities. The team decided to build an in-house AI call summary solution instead of purchasing Ignite licenses, saving significant licensing costs while providing **Joe Ainsworth** more customization control. **Scott** approved immediate termination of underperforming personnel including **James Tejada**. The group discussed a potential $600K Danbury-Shreveport market swap with Cumulus, though **Bill Jones** raised cash flow concerns requiring careful CapEx analysis before proceeding."

---

**DISCUSSION NOTES GENERATION:**

Create a consolidated narrative summary organized by THEME (not chronologically).

**LENGTH**: Make the discussion notes appropriate to the meeting complexity and content:
- SHORT meetings (<30 min, few topics): 200-300 words
- MEDIUM meetings (30-60 min, moderate complexity): 300-500 words
- COMPLEX meetings (60+ min, many topics/decisions): 500-800 words

The goal is comprehensive coverage of key themes, not arbitrary word limits.
Focus on quality and completeness over brevity.

Structure:
- Include 2-3 thematic subheadings (e.g., **Cost Savings**, **Personnel Decisions**, **Strategic Initiatives**)
- Each theme should be covered thoroughly with operational details and strategic context
- Reference the topic segments and extracted data
- Include important context, reasoning, and background
- Written in past tense for someone who missed the meeting
- No bullet points within paragraphs (narrative flow)

Guidelines:
- Write in past tense (the meeting already happened)
- Use professional business language
- Include specific names, numbers, and dates from the transcript
- **IMPORTANT: Bold all participant names** using **Name** markdown
- **CRITICAL: Bold the thematic subheadings** using **Subheading** markdown
- Reference the extracted data but don't just list it
- Maintain an objective, factual tone
- DO NOT include "Key Outcomes" or "Next Steps" sections (those are handled elsewhere)
- Provide operational color and strategic insights (provider names, specific rates, alternatives considered, etc.)

Example discussion_notes:
"**AI Technology Decisions**

**Scott Schatz** led discussions on leveraging AI for improving operational efficiency. The team reviewed Ignite's proposal for AI-powered call summaries but decided against it due to high licensing costs. Instead, **Scott** approved **Joe Ainsworth's** recommendation to build an in-house solution using Claude API, which would provide greater customization and cost savings.

**Personnel and Organizational Changes**

The meeting addressed several staffing decisions. **Scott** approved immediate termination of **James Tejada** and an underperforming NY engineer, with **Eric Williams** noting potential $4M in broadcast personnel cost savings. The team also discussed upcoming Teams/VoIP migration completing mid-January, which would enable further corporate cost reductions.

**Market Opportunities and Financial Analysis**

**Eric Williams** and the team evaluated a proposed $600K Danbury-Shreveport market swap with Cumulus. While the strategic benefits were clear, **Bill Jones** raised concerns about Danbury cash flow implications and emphasized the need for careful CapEx analysis before proceeding. **Eric** also highlighted that trade revenue reached $3.8M this year versus the typical $1M baseline, demonstrating strong performance."

---

**TRANSCRIPT:**

{transcript}

---

**NOW GENERATE THE COMPLETE JSON RESPONSE:**

Remember:
- Return ONLY a valid JSON object with the exact structure shown above
- No text before or after
- Start with {{ and end with }}
- **PRESERVE ALL MARKDOWN FORMATTING** including bold participant names and subheadings
- Do NOT echo back the transcript in your response
- Ensure all strings are properly quoted and escaped
- Ensure all JSON arrays and objects are properly closed
- Discussion notes should be appropriate length for meeting complexity (200-800 words)
"""
