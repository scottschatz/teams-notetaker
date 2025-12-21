"""
Enterprise Intelligence Classification Prompt.

This prompt is used as a 7th extraction stage to classify meetings
and extract metadata for enterprise intelligence and future chatbot capabilities.

Extracts:
- Meeting classification (type, category, seniority)
- Sentiment and tone analysis
- Counts and metrics
- Topics and entities
- Structured data (concerns, blockers, market intelligence)
- External detection (clients, competitors)
- Quick filtering flags
"""

CLASSIFICATION_PROMPT = """Analyze this meeting transcript and extract comprehensive classification metadata.

You MUST return ONLY a valid JSON object. No explanatory text before or after. No markdown code blocks. Start with {{ and end with }}.

**PARTICIPANT NAMES:**
{participant_names}

**REQUIRED OUTPUT STRUCTURE:**

{{
  "classification": {{...}},
  "sentiment": {{...}},
  "counts": {{...}},
  "topics": {{...}},
  "structured_data": {{...}},
  "external_detection": {{...}},
  "flags": {{...}}
}}

---

**CLASSIFICATION (Meeting Type & Context):**

{{
  "classification": {{
    "meeting_type": "...",
    "meeting_category": "...",
    "seniority_level": "...",
    "department_context": "...",
    "is_onboarding": false,
    "is_coaching": false,
    "is_sales_meeting": false,
    "is_support_call": false
  }}
}}

Fields:
- **meeting_type**: One of: sales_call, internal_sync, onboarding, coaching, planning_session, status_update, problem_solving, strategic_review, financial_review, performance_review, training, all_hands, customer_meeting, vendor_discussion, hiring_interview, post_mortem, brainstorm, retrospective, demo, kickoff, social, unknown
- **meeting_category**: One of: internal, external_client, external_vendor, mixed
- **seniority_level**: One of: c_suite, executive, management, individual_contributor, mixed
- **department_context**: Inferred department(s) like "Engineering", "Sales & Marketing", "Finance"
- **is_onboarding**: true if new hire or customer onboarding
- **is_coaching**: true if 1:1 coaching/mentoring/feedback session
- **is_sales_meeting**: true if sales/revenue/deal focused
- **is_support_call**: true if customer support issue

---

**SENTIMENT & TONE:**

{{
  "sentiment": {{
    "overall_sentiment": "...",
    "urgency_level": "...",
    "consensus_level": "...",
    "meeting_effectiveness": "...",
    "communication_style": "...",
    "energy_level": "..."
  }}
}}

Fields:
- **overall_sentiment**: One of: positive, neutral, negative, mixed
- **urgency_level**: One of: critical, high, medium, low
- **consensus_level**: One of: unanimous, strong_agreement, split, contentious, not_applicable
- **meeting_effectiveness**: One of: highly_productive, productive, neutral, unproductive, waste
- **communication_style**: One of: formal, professional, casual, mixed
- **energy_level**: One of: high, medium, low

---

**COUNTS & METRICS:**

{{
  "counts": {{
    "action_item_count": 0,
    "decision_count": 0,
    "open_question_count": 0,
    "blocker_count": 0,
    "follow_up_required": false
  }}
}}

Fields:
- **action_item_count**: Number of action items/todos identified
- **decision_count**: Number of decisions made
- **open_question_count**: Unresolved questions at meeting end
- **blocker_count**: Blockers/risks/issues identified
- **follow_up_required**: true if explicit follow-up meeting needed

---

**TOPICS & ENTITIES:**

{{
  "topics": {{
    "topics_discussed": ["budget", "Q4 planning", "hiring"],
    "projects_mentioned": ["Project Phoenix", "Website Redesign"],
    "products_mentioned": ["Salesforce", "Teams", "our CRM"],
    "technologies_discussed": ["Python", "AWS", "Azure"],
    "people_mentioned": ["John Smith", "CEO", "the board"],
    "deadlines_mentioned": [{{"date": "2024-01-15", "context": "launch deadline"}}],
    "financial_mentions": [{{"amount": 50000, "currency": "USD", "context": "budget request"}}]
  }}
}}

Guidelines:
- **topics_discussed**: Main themes/subjects (max 10)
- **projects_mentioned**: Specific project names referenced
- **products_mentioned**: Products, tools, software discussed
- **technologies_discussed**: Tech platforms/languages/frameworks
- **people_mentioned**: People referenced but NOT in meeting
- **deadlines_mentioned**: Specific dates with context
- **financial_mentions**: Dollar amounts with context

---

**STRUCTURED DATA:**

{{
  "structured_data": {{
    "concerns": [...],
    "blockers": [...],
    "market_intelligence": {{...}},
    "training_content": [...]
  }}
}}

**concerns** array - complaints, issues, worries raised:
{{
  "description": "Customer complained about slow response times",
  "category": "customer_complaint",
  "severity": "high",
  "resolution_status": "discussed"
}}
- category: product_issue, process_problem, resource_constraint, interpersonal, technical_blocker, market_threat, customer_complaint
- severity: critical, high, medium, low
- resolution_status: unresolved, discussed, resolved

**blockers** array - obstacles preventing progress:
{{
  "description": "Waiting for legal approval on contract",
  "owner": "Legal team",
  "target_date": "2024-01-20",
  "status": "pending"
}}

**market_intelligence** object:
{{
  "competitors": ["Competitor A was mentioned as doing X"],
  "trends": ["Market shifting toward AI solutions"],
  "insights": ["Industry consolidation expected"]
}}

**training_content** array - knowledge transfer detected:
{{
  "topic": "How to use the new CRM",
  "presenter": "Sarah",
  "target_audience": "New sales team"
}}

---

**EXTERNAL DETECTION:**

{{
  "external_detection": {{
    "has_external_participants": false,
    "external_company_names": ["Acme Corp", "Client Inc"],
    "client_names": ["BigCo", "Enterprise Customer"],
    "competitor_names": ["Competitor A", "Rival Inc"]
  }}
}}

Fields:
- **has_external_participants**: true if any non-internal participants
- **external_company_names**: External companies mentioned or participating
- **client_names**: Identified client organizations
- **competitor_names**: Competitors mentioned by name

---

**FLAGS (Quick Filtering):**

{{
  "flags": {{
    "has_financial_discussion": false,
    "has_deadline_pressure": false,
    "has_escalation": false,
    "has_customer_complaint": false,
    "has_technical_discussion": false,
    "is_confidential": false
  }}
}}

Fields:
- **has_financial_discussion**: true if money/budget/pricing discussed
- **has_deadline_pressure**: true if tight deadlines or time pressure mentioned
- **has_escalation**: true if issue escalated or needs urgent attention
- **has_customer_complaint**: true if customer expressed issue/frustration
- **has_technical_discussion**: true if engineering/technical content
- **is_confidential**: true if sensitive/NDA/confidential content detected

---

**TRANSCRIPT:**

{transcript}

---

**NOW GENERATE THE COMPLETE JSON RESPONSE:**

Remember:
- Return ONLY a valid JSON object with the exact structure shown above
- No text before or after
- Start with {{ and end with }}
- Use empty arrays [] for lists with no items
- Use null for optional fields that don't apply
- Ensure all strings are properly quoted and escaped
"""
