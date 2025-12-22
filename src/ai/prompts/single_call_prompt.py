"""
Single-call comprehensive meeting summarization prompt for Claude Haiku.

IMPORTANT: This is the HAIKU FALLBACK PROMPT.
- Primary model: Gemini 3 Flash (see gemini_prompt.py)
- Fallback model: Claude Haiku 4.5 (this prompt)

When to use this fallback:
- Gemini API is unavailable (quota exceeded, outage, etc.)
- Gemini returns invalid JSON that cannot be parsed
- GOOGLE_API_KEY is not configured

The SingleCallSummarizer in summarizer.py will automatically fall back
to this prompt when Gemini fails.

This prompt is tuned for Claude Haiku's behavior:
- Haiku naturally captures implicit action items (no explicit instruction needed)
- Haiku adapts section count to content (2-3 is a minimum)
- Haiku provides good business context without extensive prompting
"""

SINGLE_CALL_COMPREHENSIVE_PROMPT = """Analyze this meeting transcript and extract ALL structured information in a single JSON response.

You MUST return ONLY a valid JSON object. No explanatory text before or after. No markdown code blocks. Start with {{ and end with }}.

---

**CRITICAL PRESERVATION RULES (APPLY TO ALL SECTIONS):**

These rules ensure NO detail is lost. Each section is INDEPENDENT - do not reduce detail in one section to make room for another.

1. **ENTITY PRESERVATION**: If a person is associated with a specific number or metric, that association MUST appear in the output. Example: "Erica has 6 projects" → key_numbers MUST include "6 - Number of projects Erica Anderson has in development"

2. **ACTION ITEM GRANULARITY**: NEVER combine action items for multiple people into one entry. If "James and Joe should commit code frequently" is mentioned, create TWO separate action items - one for James, one for Joe.

3. **NUMERIC COMPLETENESS**: Every significant number mentioned (costs, counts, percentages, timeframes) MUST appear in key_numbers. If in doubt, include it.

4. **THEMATIC EXHAUSTIVENESS**: Discussion notes MUST cover ALL distinct topics discussed. If the meeting covered 5 different areas, create 5 themed sections. Do not merge or skip topics for brevity.

5. **SECTION INDEPENDENCE**: The ai_answerable_questions section is a BONUS section that DOES NOT reduce the detail level of any other section. Treat it as additive, not competitive.

6. **ENTITY ANCHORING**: Important proper nouns (tool names like Nginx, Zetta, Red Canary, FastAPI, Datto) and specific numbers (50 projects, 40% savings, $2M budget) are "anchors." Each anchor should appear in at least TWO sections - once in a structured field (key_numbers, highlights, decisions) AND once in discussion_notes with context.

7. **DECISION JUSTIFICATION**: Every decision in the decisions array MUST include technical or business justification in the reasoning field. Not just "what" was decided, but "why" (e.g., "to prevent port conflicts", "to maintain detail quality", "due to cost constraints").

8. **CROSS-REFERENCE COHERENCE**: Before finalizing, verify that every specific detail mentioned in executive_summary is explained with context in discussion_notes. If the summary mentions "Nginx proxy manager," the discussion notes must explain what it does and why it matters.

---

**MEETING DATE CONTEXT:**

This meeting occurred on: {meeting_date}

Use this date to resolve relative deadlines in action items:
- "Tomorrow" → the day after {meeting_date}
- "Next week" → the week after {meeting_date}
- "Friday" → the Friday of or after {meeting_date}

Always convert relative dates to absolute dates (e.g., "Tomorrow" → "Monday, December 23, 2025").

---

**PARTICIPANT NAMES (USE THESE EXACT SPELLINGS):**

The following are the correct spellings of participant names from the meeting invite. When mentioning anyone in your summary, use these EXACT spellings (the transcript may have phonetic misspellings):

{participant_names}

**COMPANY EXECUTIVES (ALWAYS USE THESE EXACT SPELLINGS):**

These are key executives frequently referenced in meetings. ALWAYS use these exact spellings even if the transcript has phonetic misspellings (e.g., "Eric" should be "Erik Hellum"):

- Bill Wilson - Chief Executive Officer (CEO)
- Steven Price - President
- Stuart Rosenstein - Chief Financial Officer (CFO)
- Erik Hellum - Chief Operating Officer (COO)
- Scott Schatz - Executive Vice President, Technology
- Claire Yenicay - Executive Vice President, Investor Relations and Corporate Communications
- Heather Hagar - Senior Vice President, Communications

**REQUIRED OUTPUT STRUCTURE:**

{{
  "action_items": [...],      // Array of action item objects
  "decisions": [...],          // Array of decision objects (8-10 max)
  "highlights": [...],         // Array of key moment objects (5-8 max)
  "key_numbers": [...],        // Array of quantitative metric objects (max 20)
  "executive_summary": "...",  // String (50-125 words, varies by meeting complexity)
  "discussion_notes": "...",   // String (appropriate length based on meeting complexity)
  "ai_answerable_questions": [...] // Array of ALL questions AI can help answer (no limit)
}}

---

**ACTION ITEMS EXTRACTION:**

Extract ALL action items, tasks, and to-dos. For each provide:
- **description**: Clear, actionable task description (what needs to be done)
- **assignee**: Person responsible (full name if mentioned, or "Unassigned" if unclear)
- **deadline**: Due date or timeframe. Use these formats:
  - Specific date: "Tuesday, December 23, 2025" (convert relative dates like "Tomorrow")
  - Timeframe: "End of week", "By Friday"
  - Ongoing/habit items: "Ongoing practice" (for recurring behaviors like "commit code frequently")
  - Unknown: "Not specified"
- **context**: Why this task is needed (1-2 sentences of background)
- **timestamp**: When it was mentioned in the meeting (format: H:MM:SS)

Guidelines:
- Only include EXPLICIT action items (not general discussions)
- Look for phrases like: "can you...", "please...", "we need to...", "I'll...", "[name] will..."
- **NEVER COMBINE**: If the same task applies to multiple people, create SEPARATE action items for each person
- Include both immediate tasks and follow-up items
- Do NOT include hypothetical or conditional tasks ("if we decide to...")
- **ORDERING**: List one-time tasks with specific deadlines FIRST, then ongoing/habit items LAST
- **CRITICAL: Bold all participant names using **Name** markdown syntax**
- **CRITICAL: Verify assignee attribution by checking the <v SpeakerName> tags - only assign to people who explicitly accepted the task**
- **CRITICAL: Prefer MORE action items over fewer. If in doubt about whether something is an action item, include it.**

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
- **PERSON-SPECIFIC METRICS**: If a number is associated with a specific person (e.g., "Erica has 6 projects", "Joe tested 3 configurations"), include the person's name in the context
- Round approximate values appropriately (e.g., "$1M" not "$1,000,000")
- Include context that makes the number meaningful
- Sort by magnitude (largest to smallest) or logical grouping
- Maximum 20 entries (but INCLUDE all person-specific counts even if over 20)
- Skip trivial numbers (page numbers, timestamps, percentages under 5%)
- **CRITICAL: Bold all participant names using **Name** markdown syntax**
- **CRITICAL: When in doubt, INCLUDE the number. More is better than fewer.**

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
- Include as many thematic subheadings as the meeting requires (minimum 3, more for complex meetings)
- **EXHAUSTIVE COVERAGE**: If 5 distinct topics were discussed, create 5 sections. Do NOT merge topics for brevity.
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

**AI-ANSWERABLE QUESTIONS EXTRACTION:**

Identify questions raised during the meeting that AI can help answer with general knowledge. For each provide:
- **question**: The question as stated or implied (clear, specific)
- **asked_by**: Who raised it (full name)
- **context**: Why this came up in the meeting (1 sentence)
- **answer**: Your helpful answer (2-4 sentences with specific details, tools, or approaches)
- **follow_up_prompts**: Array of 1-2 suggested prompts for deeper research

**INCLUDE questions about (any domain):**
- External tools, software, or services ("Does [tool] have a feature for X?")
- Industry best practices ("What's the standard approach for X?")
- General knowledge ("How does X work?", "What is X?")
- Regulations, compliance, standards ("What are the requirements for X?")
- Market rates, benchmarks ("What's typical pricing for X?")
- Technology or methodology comparisons ("Should we use X or Y?")

**EXCLUDE questions about (filter these out):**
- Internal company data ("How many X do we have?", "What's our budget?")
- Specific people's actions or status ("Is [person] doing X?", "Did [person] finish?")
- Company-specific decisions ("Did we approve X?", "What did leadership decide?")
- Internal processes or logistics ("Where is our meeting room?")
- Rhetorical or social questions ("How are you?", "Right?")

Guidelines:
- Include ALL AI-answerable questions identified (no limit - this section is high value)
- ONLY include questions where your answer would genuinely help the team
- Provide SPECIFIC, ACTIONABLE answers (tool names, approaches, resources)
- If you're not confident in an answer, still provide it with appropriate caveats
- Follow-up prompts should help them dig deeper on the topic
- **CRITICAL: Bold all participant names using **Name** markdown syntax**

Example ai_answerable_questions entry:
{{
  "question": "Does GitHub have a feature to create a showcase or library page for our repositories?",
  "asked_by": "Scott Schatz",
  "context": "Team wants to create a centralized catalog of internal tools for stakeholder visibility",
  "answer": "GitHub doesn't have a built-in showcase feature, but there are excellent options: (1) **Backstage.io** - Spotify's open-source developer portal designed exactly for this, with service catalogs, documentation, and discoverability; (2) **GitHub Pages** with the GitHub API to build a custom portal; (3) **GitHub Organization README** for a simpler profile page. Backstage.io is the most comprehensive solution for internal tool discovery.",
  "follow_up_prompts": [
    "How do I set up Backstage.io for a small team with 50 repositories?",
    "Show me examples of GitHub Pages sites that showcase internal tools"
  ]
}}

Example (finance domain):
{{
  "question": "How should we account for software subscription costs under GAAP?",
  "asked_by": "Bill Jones",
  "context": "Discussing whether to capitalize or expense new SaaS platform costs",
  "answer": "Under ASC 350-40, most SaaS costs are expensed as incurred since you don't control the software. However, implementation costs can be capitalized if they meet criteria (application development stage, probable future benefit). Hosting costs are always expensed. The key distinction is whether you have the right to take possession of the software or it's purely a service arrangement.",
  "follow_up_prompts": [
    "What implementation costs can be capitalized for SaaS under ASC 350-40?",
    "How do I document SaaS capitalization decisions for auditors?"
  ]
}}

If no AI-answerable questions are identified, use: "ai_answerable_questions": []

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

**FINAL PRESERVATION CHECK:**
Before returning, verify:
□ Every person mentioned with a specific number has that association in key_numbers
□ No action items combine multiple assignees - each person gets their own entry
□ Discussion notes cover ALL distinct topics (count them - if 5 topics, 5 sections)
□ The ai_answerable_questions section did NOT cause reduction in any other section
□ Key tool/product names appear in BOTH a structured field AND discussion_notes (Entity Anchoring)
□ Every decision has technical/business justification in reasoning, not just "what" but "why"
□ Every specific detail in executive_summary is explained in discussion_notes (Cross-Reference)
□ All relative dates (Tomorrow, Next week) are converted to absolute dates
"""
