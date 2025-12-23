# LLM Comparison Testing for Meeting Summarization

**Date:** December 20, 2025 (Updated: December 23, 2025)
**Purpose:** Evaluate alternative LLMs to Claude Haiku 4.5 for cost optimization
**Status:** ⚠️ HAIKU PRIMARY - Gemini disabled after quality issues (infrastructure ready for re-enablement)

---

## Executive Summary

| Model | Cost/Summary | Speed | Success Rate | Quality | Status |
|-------|--------------|-------|--------------|---------|--------|
| **Claude Haiku 4.5** | ~$0.06 | 2-6s | 100% | 9.5/10 | ✅ **PRIMARY** |
| Gemini 3 Flash | ~$0.03 | 15-24s | 100% | 8.5/10 | ⏸️ Disabled (quality issues) |
| GPT-5 Mini | $0.012 | 90-130s | 100%* | 9.1/10 | Tested, not deployed |
| Gemini 2.5 Flash | - | 34s | 20% | N/A | ❌ Not viable |

*With optimized prompt and 16K token limit

**NOTE:** Cost estimates updated Dec 23, 2025 to reflect actual pricing:
- Haiku: $1.00/MTok input, $5.00/MTok output (~$0.06/summary, not $0.004)
- Gemini: $0.50/MTok input, $3.00/MTok output (~$0.03/summary, 48% cheaper)

### Production Architecture (Current: Dec 2025)

**CURRENT STATUS: Haiku Primary (Gemini Disabled)**

```
User Request
    ↓
┌─────────────────────────────────┐
│ USE_GEMINI_PRIMARY = False      │  ← Toggle in src/ai/summarizer.py
└───────────┬─────────────────────┘
            ↓
┌─────────────────────────────────┐
│ Claude Haiku 4.5 (ONLY)         │  ← Primary path (Gemini disabled)
│ - Uses single_call_prompt.py    │
│ - $0.06/summary                  │
│ - 100% reliable                  │
└─────────────────────────────────┘
```

**AVAILABLE ARCHITECTURE (if Gemini re-enabled):**

Set `USE_GEMINI_PRIMARY = True` in `src/ai/summarizer.py`:

```
User Request
    ↓
┌─────────────────────────┐
│ Try Gemini 3 Flash      │  ← Primary (48% cheaper)
│ - Uses gemini_prompt.py │
└───────────┬─────────────┘
            ↓
    ┌───────┴───────┐
    │ Success?      │
    └───────┬───────┘
            │
    ┌───────┴───────┐
    │Yes            │No (API error, quota, invalid JSON)
    ↓               ↓
  Return        ┌─────────────────────────┐
  Result        │ Fall back to Haiku      │  ← Reliable backup
                │ - Uses single_call_prompt.py │
                └─────────────────────────┘
```

**WHY GEMINI IS DISABLED:**
- Duration extraction issues: "None minutes" in output
- Lower detail quality in discussion notes compared to Haiku
- Speaker participation stats inferior to Haiku
- Infrastructure remains in place for easy re-enablement

### Key Files

| File | Purpose |
|------|---------|
| `src/ai/gemini_client.py` | Gemini API client |
| `src/ai/prompts/gemini_prompt.py` | Gemini-optimized prompt |
| `src/ai/prompts/single_call_prompt.py` | Haiku fallback prompt |
| `src/ai/summarizer.py` | SingleCallSummarizer with fallback logic |

### Cost Savings

**Pricing (Dec 2025):**
- Gemini: $0.50/MTok input, $3.00/MTok output
- Haiku: $1.00/MTok input, $5.00/MTok output

**At Scale (52,000 meetings/year):**
- Previous (Haiku only): ~$2,340/year
- New (Gemini + Haiku fallback): ~$1,222/year
- **Annual savings: ~$1,118 (48%)**

### Environment Configuration

```bash
# Required for Gemini (primary)
GOOGLE_API_KEY=your_google_api_key

# Required for Haiku (fallback)
CLAUDE_API_KEY=your_anthropic_api_key
```

If `GOOGLE_API_KEY` is not set, the system automatically uses Haiku only.

---

## Current Production Setup

### Model: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)

**Pricing:**
- Input: $1.00 per million tokens
- Output: $5.00 per million tokens

**Performance:**
- Average tokens: 18k input, 3.8k output per summary
- Cost per summary: ~$0.037
- Response time: 2-6 seconds
- Success rate: 100%

**Prompt:** Single-call comprehensive extraction
- Location: `src/ai/prompts/single_call_prompt.py`
- Approach: One API call extracts all structured data + narrative

**Strengths:**
- Zero tool-calling failures
- Excellent JSON reliability
- Fast response times
- Comprehensive narrative output
- Proven at production scale

---

## GPT-5 Mini Testing

### Model: `gpt-5-mini-2025-08-07`

**Pricing:**
- Input: $0.25 per million tokens
- Output: $2.00 per million tokens

### Test 1: Original Haiku Prompt (Failed)

**Results:**
- Success rate: 60% (3/5 samples)
- Critical failures on transcripts >9,000 words
- Failures returned empty responses (0 characters)
- Still billed for tokens despite no output

**Failure Analysis:**
- Silent failure mode - API returns success but empty content
- GPT-5 Mini is a reasoning model that uses internal tokens
- 8K max_tokens was insufficient for reasoning overhead

### Test 2: Optimized Prompt (Success)

**Prompt Changes (per ChatGPT recommendations):**
1. Removed markdown formatting requirements from extraction fields
2. Simplified cognitive load with clearer structure
3. Hard limits instead of ranges ("NO MORE THAN 8" vs "8-10")
4. Cleaner section separators
5. Explicit JSON validity reminders

**Critical Fix:** Increased `max_completion_tokens` from 8,000 to 16,000

**Results:**
- Success rate: 100% (5/5 samples)
- Previously failed 9,563-word transcript: SUCCESS
- Previously failed 14,311-word transcript: SUCCESS

**Performance Comparison:**

| Metric | GPT-5 Mini | Claude Haiku 4.5 |
|--------|-----------|------------------|
| Speed | 90-130s | 2-6s |
| Cost | $0.012 | $0.037 |
| Quality | 9.1/10 | 9.5/10 |
| JSON reliability | 100%* | 100% |

*With optimized prompt only

### GPT-5 Mini Optimized Prompt

```
You are an information extraction and summarization engine.

Analyze the meeting transcript below and return ALL requested information
as a SINGLE valid JSON object.

CRITICAL OUTPUT RULES:
- Return ONLY valid JSON
- No text before or after the JSON
- No markdown code blocks
- Start with { and end with }
- Ensure all strings are properly quoted
- Ensure all arrays and objects are properly closed

----------------------------------------------------------------
PARTICIPANT NAMES (USE THESE EXACT SPELLINGS):

Use ONLY the following spellings when referring to participants.
If the transcript contains phonetic or incorrect spellings, normalize
to these names.

{participant_names}

----------------------------------------------------------------
REQUIRED OUTPUT STRUCTURE (EXACT):

{
  "action_items": [],
  "decisions": [],
  "highlights": [],
  "key_numbers": [],
  "executive_summary": "",
  "discussion_notes": ""
}

----------------------------------------------------------------
ACTION ITEMS

Extract ALL explicit action items or tasks.

Include ONLY tasks that were clearly assigned or accepted.
Do NOT infer or include hypothetical tasks.

Each action item object must include:
- description: clear, actionable task
- assignee: full participant name, or "Unassigned"
- deadline: date or timeframe, or "Not specified"
- context: 1-2 sentences explaining why the task exists
- timestamp: H:MM:SS

Attribution rules:
- Assign tasks ONLY to speakers who explicitly accepted responsibility
- Verify attribution using <v SpeakerName> tags in the transcript

If none exist, return an empty array.

----------------------------------------------------------------
DECISIONS

Identify the MOST IMPORTANT FINAL decisions made in this meeting.
Return NO MORE THAN 8 decisions.

Each decision object must include:
- decision: clear statement of what was decided
- rationale_one_line: brief reason (10 words max)
- reasoning: 1-2 sentences explaining why
- impact: 1 sentence describing what this decision affects
- timestamp: MM:SS

Rules:
- Include FINAL decisions only (not proposals or discussions)
- If a decision changed, include ONLY the final version
- Prioritize business-impact decisions

If none exist, return an empty array.

----------------------------------------------------------------
HIGHLIGHTS (KEY MOMENTS)

Return 5-8 of the most important moments someone would want to revisit.

Each highlight must include:
- description: single concise sentence (max ~20 words)
- timestamp: MM:SS
- type: one of [decision, action_item, insight, milestone, concern, question]

Rules:
- Prioritize moments with business impact
- Skip procedural or low-value moments

If none exist, return an empty array.

----------------------------------------------------------------
KEY NUMBERS

Extract significant quantitative metrics mentioned.

Each key number object must include:
- value: formatted number (e.g., "$4M", "40%", "15 days")
- unit: dollars, percent, days, count, users, etc.
- context: brief explanation (max 15 words)
- magnitude: numeric value for sorting (e.g., 4000000)

Rules:
- Include financial, operational, growth, and time-based metrics
- Skip trivial numbers
- Return no more than 20, prioritized by importance

If none exist, return an empty array.

----------------------------------------------------------------
EXECUTIVE SUMMARY

Write a concise executive-ready prose summary.

Guidelines:
- 75-110 words, depending on meeting complexity
- Past tense
- Professional business tone
- Focus on what happened and why it matters
- Reference specific people, decisions, and numbers
- No bullet points

----------------------------------------------------------------
DISCUSSION NOTES

Write a thematic narrative summary organized by topic (not chronology).

Guidelines:
- 300-600 words depending on complexity
- Organize into 2-3 clear thematic sections
- Include operational details, reasoning, and context
- Past tense, objective tone
- No bullet points inside paragraphs

----------------------------------------------------------------
TRANSCRIPT:

{transcript}

----------------------------------------------------------------
FINAL REMINDERS:
- Output ONLY the JSON object
- Follow the exact structure provided
- Do NOT repeat the transcript
- Do NOT include explanations or commentary
```

### GPT-5 Mini Implementation Notes

If implementing GPT-5 Mini:

```python
from openai import OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

response = client.chat.completions.create(
    model="gpt-5-mini-2025-08-07",
    messages=[
        {"role": "system", "content": "You are an information extraction engine."},
        {"role": "user", "content": optimized_prompt}
    ],
    max_completion_tokens=16000,  # CRITICAL: Must be 16K, not 8K
    # temperature not supported by this model
)
```

**Post-processing required:**
- Add **bold** markdown to participant names after extraction
- Validate JSON and retry with repair prompt if invalid

---

## GPT-5 Nano (Pending Testing)

### Model: `gpt-5-nano`

**Pricing:**
- Input: $0.05 per million tokens
- Output: $0.40 per million tokens
- **20x cheaper than Haiku**

**Expected Cost:** ~$0.002 per summary

**Testing Plan:**
1. Use same optimized prompt as GPT-5 Mini
2. Test on same 5 sample transcripts
3. Focus on: JSON validity, extraction accuracy, prose quality
4. May need further prompt simplification

---

## Google Gemini Testing

### Gemini 2.5 Flash (FAILED - Do Not Use)

**Model:** `models/gemini-2.5-flash`

**Pricing:**
- Input: $0.10 per million tokens
- Output: $0.40 per million tokens

**Results:**
- Success rate: **20% (1/5 samples)** ❌
- Failure mode: Unterminated strings, JSON truncation
- Repair success: 0% (repairs also failed)

**Why It Failed:**
1. Systematic JSON malformation (unterminated strings mid-sentence)
2. Invalid timestamp formats (`"07:03:842"` instead of `"0:07:03"`)
3. Premature output truncation
4. Zero repair success rate

**Verdict:** Not viable for production use.

---

### Gemini 3 Flash Preview (SUCCESS - Recommended)

**Model:** `models/gemini-3-flash-preview`

**Pricing:**
- Input: ~$0.10 per million tokens
- Output: ~$0.40 per million tokens
- **Average cost: $0.004 per transcript** (89% cheaper than Haiku)

**Results:**
- Success rate: **100% (5/5 samples)** ✅
- JSON validity: 100%
- Schema completeness: 100%
- Repairs needed: 0

**Performance Metrics:**

| Transcript | Words | Latency | Cost | Action Items | Decisions | Highlights | Key Numbers |
|------------|-------|---------|------|--------------|-----------|------------|-------------|
| #47 | 14,311 | 24.0s | $0.0063 | 8 | 5 | 6 | 8 |
| #49 | 11,290 | 14.0s | $0.0037 | 2 | 1 | 5 | 10 |
| #52 | 10,849 | 20.8s | $0.0028 | 5 | 5 | 6 | 7 |
| #61 | 9,563 | 17.8s | $0.0040 | 4 | 2 | 6 | 4 |
| #46 | 8,591 | 20.6s | $0.0038 | 6 | 3 | 6 | 7 |

**Averages:**
- Latency: 19.4s (3-10x faster than GPT-5 Mini)
- Cost: $0.0041 per transcript
- Total tokens: ~35K input, ~1.5K output

**Key Improvements Over 2.5:**
- 80 percentage point improvement in success rate (20% → 100%)
- Zero JSON syntax errors
- Complete output (no truncation)
- Faster average latency (34s → 19s)

### Gemini 3 Flash Extraction-Only Prompt

```
You are a structured information extraction engine.

Analyze the meeting transcript and return ONLY the structured data
defined below as a SINGLE valid JSON object.

CRITICAL OUTPUT RULES:
- Output JSON only
- No explanations, no commentary
- No markdown blocks
- Start with { and end with }
- Ensure valid JSON (quotes, commas, arrays closed)

--------------------------------------------------
PARTICIPANT NAMES (NORMALIZE TO THESE):

Use ONLY these spellings when referencing participants.
Correct transcript misspellings to these names.

{participant_names}

--------------------------------------------------
REQUIRED JSON STRUCTURE (EXACT):

{
  "action_items": [],
  "decisions": [],
  "highlights": [],
  "key_numbers": []
}

--------------------------------------------------
ACTION ITEMS

Extract ALL explicit tasks that were clearly assigned or accepted.

Include ONLY confirmed action items.
Do NOT infer or speculate.

Each action item object:
{
  "description": "",
  "assignee": "",
  "deadline": "",
  "context": "",
  "timestamp": ""
}

Rules:
- Assign ONLY if a speaker explicitly accepted responsibility
- Use <v SpeakerName> tags to verify attribution
- Use "Unassigned" if unclear
- Timestamp format: H:MM:SS

If none exist, return [].

--------------------------------------------------
DECISIONS

Extract the MOST IMPORTANT FINAL decisions.
Return NO MORE THAN 6.

Each decision object:
{
  "decision": "",
  "rationale_one_line": "",
  "reasoning": "",
  "impact": "",
  "timestamp": ""
}

Rules:
- Include FINAL decisions only
- Exclude proposals or discussions
- Prioritize business impact
- Timestamp format: MM:SS

If none exist, return [].

--------------------------------------------------
HIGHLIGHTS

Return 5–7 key moments worth revisiting.

Each highlight object:
{
  "description": "",
  "timestamp": "",
  "type": ""
}

Type must be one of:
decision | action_item | insight | milestone | concern | question

--------------------------------------------------
KEY NUMBERS

Extract significant quantitative metrics.

Each key number object:
{
  "value": "",
  "unit": "",
  "context": "",
  "magnitude": 0
}

Rules:
- Include financial, operational, growth, and time metrics
- Skip trivial numbers
- Max 15 entries

--------------------------------------------------
TRANSCRIPT:

{transcript}

--------------------------------------------------
FINAL CHECK:
- Output JSON only
- Match structure exactly
- Do not repeat transcript
```

### Gemini Implementation Notes

```python
import google.generativeai as genai
import os

genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
model = genai.GenerativeModel('models/gemini-3-flash-preview')

response = model.generate_content(prompt)
result = response.text

# Parse JSON
import json
data = json.loads(result)

# Access token usage
usage = response.usage_metadata
print(f"Input tokens: {usage.prompt_token_count}")
print(f"Output tokens: {usage.candidates_token_count}")
```

**Note:** The `google.generativeai` package is deprecated. For production, migrate to `google.genai`.

---

## Cost Projections at Scale

### Corrected Pricing (Dec 2025)

| Model | Input $/MTok | Output $/MTok | Per Summary | Status |
|-------|--------------|---------------|-------------|--------|
| **Gemini 3 Flash** | $0.50 | $3.00 | ~$0.0025 | ✅ **PRIMARY** |
| Claude Haiku 4.5 | $1.00 | $5.00 | ~$0.004 | ✅ FALLBACK |
| GPT-5 Mini | $0.25 | $2.00 | ~$0.012 | Tested |

### Annual Cost (52,000 meetings/year)

| Model | Per Summary | Annual Cost | vs Haiku | Status |
|-------|-------------|-------------|----------|--------|
| Claude Haiku 4.5 | $0.004 | $2,340 | baseline | ✅ Fallback |
| **Gemini 3 Flash** | $0.0025 | $1,222 | **-48%** | ✅ **Primary** |

### Cost Savings Summary

Switching from Claude Haiku 4.5 to Gemini 3 Flash:
- **$1,118/year** savings at scale (52,000 meetings)
- **48% reduction** in per-summary costs
- Quality score: 9/10 (near parity with Haiku's 9.5/10)
- 100% JSON reliability maintained

---

## Quality Assessment Framework

### Scoring Criteria (1-10)

| Dimension | Weight | What We Evaluate |
|-----------|--------|------------------|
| JSON Validity | 20% | Parses without errors |
| Executive Summary | 20% | Concise, accurate, professional |
| Discussion Notes | 20% | Organized, detailed, thematic |
| Action Items | 15% | Complete, correctly attributed |
| Decisions | 10% | Accurate, impact documented |
| Key Numbers | 10% | Correctly extracted, contextual |
| Highlights | 5% | Relevant, well-timestamped |

### Quality Comparison Results

| Model | JSON | Exec | Notes | Actions | Decisions | Numbers | Overall |
|-------|------|------|-------|---------|-----------|---------|---------|
| Haiku 4.5 | 10 | 9.5 | 9.5 | 9 | 9 | 9 | **9.5** |
| GPT-5 Mini* | 10 | 9 | 9 | 9 | 9 | 9 | **9.1** |

*With optimized prompt

---

## Lessons Learned

### 1. Prompt Engineering > Model Selection
The same model (GPT-5 Mini) went from 40% to 100% success rate with prompt optimization. Always tune the prompt before switching models.

### 2. Token Limits Are Model-Specific
- Haiku: 8K tokens sufficient
- GPT-5 Mini: Requires 16K (reasoning overhead)
- Gemini 3 Flash: Default limits work well
- Test each model's actual requirements

### 3. Markdown in Prompts Hurts Smaller Models
Removing `**bold**` requirements from extraction fields improved JSON validity by 30-50%.

### 4. Speed vs Cost Trade-offs
- Haiku: Fast (2-6s) but more expensive
- GPT-5 Mini: Slow (90-130s) but cheaper
- Gemini 3 Flash: Medium speed (15-24s), very cheap
- Consider latency requirements for real-time use cases

### 5. Failure Modes Differ
- Haiku: Rarely fails, graceful degradation
- GPT-5 Mini: Silent failures (empty responses)
- Gemini 2.5 Flash: JSON truncation and malformation
- Gemini 3 Flash: No failures observed
- Always implement fallback strategies

### 6. Model Versions Matter Dramatically
- Gemini 2.5 Flash: 20% success rate (not viable)
- Gemini 3 Flash: 100% success rate (production-ready)
- Always test the latest model versions before making decisions

### 7. Extraction-Only vs Full Prompts
- Extraction-only prompts are more reliable across models
- Prose generation (executive_summary, discussion_notes) should use stronger models
- Consider a two-stage approach: cheap model for extraction, Haiku for prose

---

## Recommended Architecture

### Current (Simple)
```
Transcript → Claude Haiku 4.5 → Summary (extraction + prose)
```

### Recommended (Cost-Optimized with Gemini 3 Flash)
```
Transcript → Gemini 3 Flash → Extraction (action_items, decisions, highlights, key_numbers)
         → Claude Haiku 4.5 → Prose (executive_summary, discussion_notes)

On Gemini failure → Fallback to Haiku for full summary
```

**Benefits:**
- 89% cost reduction on extraction (majority of token usage)
- Haiku quality preserved for user-facing prose
- Fast extraction (19s avg) + fast prose (2-6s) = ~25s total

### Alternative (Full Gemini 3 Flash)
```
Transcript → Gemini 3 Flash → Full extraction-only output

Post-process → Generate prose summary from extracted data
            → Add markdown formatting
```

**Best for:** Maximum cost savings when prose quality is less critical

### Fallback Chain (Production Safety)
```
Gemini 3 Flash → validate JSON
  ├─ success → accept
  ├─ fail → retry once
        ├─ success → accept
        └─ fail → Claude Haiku 4.5 (reliable fallback)
```

This keeps Haiku usage under 5% based on Gemini 3's 100% observed success rate.

---

## Test Artifacts Location

All test scripts and results saved to:
- `/tmp/gpt5_mini_test/` - Initial GPT-5 Mini tests
- `/tmp/gpt5_optimized_test/` - Optimized prompt tests
- `/tmp/gemini_flash_test/` - Gemini 2.5 Flash tests (failed)
- `/tmp/gemini3_flash_test/` - Gemini 3 Flash Preview tests (success)

Key files:
- `EXECUTIVE_SUMMARY.md` - Detailed analysis
- `RESULTS.md` - Test results summary
- `metrics.json` - Raw performance data
- `sample_outputs/` - Successful JSON extractions
- `failures/` - Failed outputs with error details
- `test_*.py` - Reusable test scripts

---

## Next Steps

1. [x] ~~Test Google Gemini Flash 2.0~~ - Tested 2.5 (failed) and 3.0 (success)
2. [x] ~~Optimize Gemini prompt~~ - Iteration B achieved 9/10 quality
3. [x] ~~Implement Gemini primary + Haiku fallback~~ - Deployed Dec 2025
4. [ ] Monitor production: Track model usage, fallback rate, quality metrics
5. [ ] Test GPT-5 Nano with simplified prompt (potential further savings)
6. [ ] Update to `gemini-2.0-flash` when released (currently using preview)

---

## Appendix: API Configuration Reference

### Claude (Anthropic)
```python
from anthropic import Anthropic
client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=8000,
    temperature=0.5,
    messages=[...]
)
```

### OpenAI
```python
from openai import OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
response = client.chat.completions.create(
    model="gpt-5-mini-2025-08-07",
    max_completion_tokens=16000,  # CRITICAL: Must be 16K for reasoning models
    messages=[...]
)
```

### Google (Gemini 3 Flash - Recommended)
```python
import google.generativeai as genai
import os

genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
model = genai.GenerativeModel('models/gemini-3-flash-preview')

response = model.generate_content(prompt)
result = response.text

# Token usage
usage = response.usage_metadata
input_tokens = usage.prompt_token_count
output_tokens = usage.candidates_token_count

# Calculate cost (~$0.10/MTok input, ~$0.40/MTok output)
cost = (input_tokens * 0.10 + output_tokens * 0.40) / 1_000_000
```

**Note:** The `google.generativeai` package is deprecated. For production, migrate to `google.genai`.

---

## Summary: Model Comparison Matrix

| Model | Cost/Summary | Speed | Success Rate | Best For |
|-------|--------------|-------|--------------|----------|
| Claude Haiku 4.5 | $0.037 | 2-6s | 100% | Full summaries, prose quality |
| GPT-5 Mini | $0.012 | 90-130s | 100%* | Backup, reasoning tasks |
| **Gemini 3 Flash** | **$0.004** | 15-24s | **100%** | **Extraction, cost savings** |
| Gemini 2.5 Flash | $0.003 | 34s | 20% | ❌ Not recommended |
| GPT-5 Nano | $0.002 | TBD | TBD | To test |

*With optimized prompt and 16K token limit

**Winner for extraction tasks: Gemini 3 Flash Preview** (89% cheaper than Haiku, 100% reliable)

---

## Gemini Prompt Optimization Results

### Iteration B - Final Production Prompt

The Gemini-optimized prompt (`gemini_prompt.py`) includes these key changes from the Haiku prompt:

1. **Explicit length minimums**: "MINIMUM 800 words" for discussion notes
   - Gemini tends to be conservative; explicit floors prevent this

2. **More sections**: "5-6 themed sections" vs Haiku's "2-3"
   - Gemini rigidly follows section count guidance

3. **Permissive action item extraction**: Captures implicit AND explicit tasks
   - "Err on the side of capturing MORE action items"

4. **WHY emphasis**: Repeated reminders to explain business impact

### Quality Comparison (Transcript 46)

| Metric | Haiku | Gemini (Iteration B) | Winner |
|--------|-------|---------------------|--------|
| Discussion Notes | 661 words | 997 words | Gemini (+51%) |
| Action Items | 20 | 10 | Tie* |
| Themed Sections | 5 | 6 | Gemini |
| JSON Validity | 100% | 100% | Tie |
| Quality Score | 9.5/10 | 9/10 | Close |

*Haiku may over-capture; Gemini's 10 items is likely more appropriate for most meetings.

---

*Document maintained by: Development Team*
*Last updated: December 23, 2025*
*Status: Haiku primary only (Gemini disabled due to quality issues, infrastructure ready for re-enablement)*

---

## Update Log

### December 23, 2025
- **Status Change**: Gemini disabled, Haiku running as primary only
- **Reason**: Quality comparison revealed Gemini issues:
  - Duration extraction: "None minutes" in outputs
  - Discussion notes: Less detailed than Haiku
  - Speaker stats: Inferior word counts and speaking time
- **Cost Impact**: ~$0.06/summary (Haiku) vs projected $0.0025/summary (Gemini)
- **Toggle**: `USE_GEMINI_PRIMARY = False` in `src/ai/summarizer.py`
- **Infrastructure**: All Gemini code remains in place for easy re-enablement
- **Pricing Correction**: Updated cost estimates to reflect actual per-summary costs ($0.06 not $0.004)
