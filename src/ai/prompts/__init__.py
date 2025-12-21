"""
Prompts Module

Contains both legacy prompts and enhanced prompts.

This package replaces the old src/ai/prompts.py file.
To avoid circular imports, we need to import directly from the parent's namespace.
"""

# Use a workaround for circular imports by importing the prompts module
# from the parent directory using sys.modules
import sys
import os

# Get the prompts.py file from parent directory
parent_dir = os.path.dirname(os.path.dirname(__file__))
prompts_file = os.path.join(parent_dir, 'prompts.py')

# Import the module directly using importlib
import importlib.util
spec = importlib.util.spec_from_file_location("legacy_prompts", prompts_file)
legacy_prompts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy_prompts)

# Export all the legacy prompts
SUMMARY_SYSTEM_PROMPT = legacy_prompts.SUMMARY_SYSTEM_PROMPT
build_summary_prompt = legacy_prompts.build_summary_prompt
build_action_items_extraction_prompt = legacy_prompts.build_action_items_extraction_prompt
build_decision_extraction_prompt = legacy_prompts.build_decision_extraction_prompt
build_topic_based_summary_prompt = legacy_prompts.build_topic_based_summary_prompt
build_technical_meeting_prompt = legacy_prompts.build_technical_meeting_prompt
build_executive_brief_prompt = legacy_prompts.build_executive_brief_prompt
estimate_token_count = legacy_prompts.estimate_token_count
validate_prompt_length = legacy_prompts.validate_prompt_length
truncate_transcript_if_needed = legacy_prompts.truncate_transcript_if_needed

# Import classification prompt for enterprise intelligence
from .classification_prompt import CLASSIFICATION_PROMPT

__all__ = [
    "SUMMARY_SYSTEM_PROMPT",
    "build_summary_prompt",
    "build_action_items_extraction_prompt",
    "build_decision_extraction_prompt",
    "build_topic_based_summary_prompt",
    "build_technical_meeting_prompt",
    "build_executive_brief_prompt",
    "estimate_token_count",
    "validate_prompt_length",
    "truncate_transcript_if_needed",
    "CLASSIFICATION_PROMPT"
]

