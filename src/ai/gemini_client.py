"""
Gemini API Client

Wrapper around Google's generative AI library for generating meeting summaries.
Used as primary model with Claude Haiku as fallback.

Gemini 3 Flash is ~48% cheaper than Claude Haiku while maintaining similar quality:
- Gemini: $0.50/MTok input, $3.00/MTok output
- Haiku: $1.00/MTok input, $5.00/MTok output
"""

import logging
import time
import os
from typing import Dict, Any, Optional
from datetime import datetime

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from ..core.exceptions import ClaudeAPIError  # Reuse exception for consistency


logger = logging.getLogger(__name__)


class GeminiAPIError(Exception):
    """Error from Gemini API call."""
    pass


class GeminiClient:
    """
    Gemini API client for generating meeting summaries.

    Uses Google's Gemini 3 Flash model which provides:
    - 48% cost savings vs Claude Haiku
    - Comparable quality with optimized prompts
    - Reliable JSON generation

    Pricing (as of Dec 2025):
    - Input: $0.50 per million tokens
    - Output: $3.00 per million tokens

    Usage:
        client = GeminiClient(api_key='...', model='gemini-2.0-flash')
        response = client.generate_text(
            system_prompt="You are a meeting summarizer",
            user_prompt="Summarize this meeting...",
            max_tokens=8000
        )
    """

    # Model pricing (per million tokens)
    MODEL_PRICING = {
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    }

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash"):
        """
        Initialize Gemini API client.

        Args:
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
            model: Model to use (default: gemini-2.0-flash)
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY not set in environment or passed to constructor")

        self.model_name = model
        self.max_retries = 3

        # Configure the client
        genai.configure(api_key=self.api_key)
        self._model = genai.GenerativeModel(model)

        logger.info(f"GeminiClient initialized (model: {model})")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Generate text completion using Gemini API.

        Args:
            system_prompt: System prompt (role/instructions)
            user_prompt: User prompt (content to process)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)

        Returns:
            Dictionary with:
                - content: Generated text
                - input_tokens: Number of input tokens
                - output_tokens: Number of output tokens
                - total_tokens: Total tokens used
                - model: Model used
                - cost: Estimated cost in USD
                - generation_time_ms: Time taken in milliseconds

        Raises:
            GeminiAPIError: If API request fails
        """
        start_time = datetime.now()

        try:
            # Combine system and user prompts (Gemini uses a single prompt)
            full_prompt = f"{system_prompt}\n\n{user_prompt}"

            # Configure generation parameters
            generation_config = GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            )

            # Generate with retry logic
            response = self._generate_with_retry(full_prompt, generation_config)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Extract response content
            content = response.text if response.text else ""

            # Get token counts from usage metadata
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)

            total_tokens = input_tokens + output_tokens
            cost = self._calculate_cost(input_tokens, output_tokens)

            logger.info(
                f"✓ Gemini generated {output_tokens} tokens in {duration_ms}ms "
                f"(input: {input_tokens}, total: {total_tokens}, cost: ${cost:.4f})"
            )

            return {
                "content": content,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "model": self.model_name,
                "cost": cost,
                "generation_time_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"Gemini API error: {e}", exc_info=True)
            raise GeminiAPIError(f"Gemini API request failed: {e}")

    def _generate_with_retry(self, prompt: str, config: GenerationConfig, retry_count: int = 0):
        """
        Make Gemini API call with retry logic for transient errors.

        Args:
            prompt: Full prompt to send
            config: Generation configuration
            retry_count: Current retry attempt

        Returns:
            API response object

        Raises:
            GeminiAPIError: If max retries exceeded
        """
        try:
            return self._model.generate_content(prompt, generation_config=config)

        except Exception as e:
            error_str = str(e).lower()

            # Check for retryable errors
            is_retryable = any(term in error_str for term in [
                'rate limit', 'quota', '429', '503', '500', 'overloaded', 'resource exhausted'
            ])

            if is_retryable and retry_count < self.max_retries:
                wait_time = min(2 ** retry_count * 5, 30)  # 5s, 10s, 20s, max 30s
                logger.warning(
                    f"Gemini API error (retryable), waiting {wait_time}s before retry "
                    f"{retry_count + 1}/{self.max_retries}: {e}"
                )
                time.sleep(wait_time)
                return self._generate_with_retry(prompt, config, retry_count + 1)
            else:
                raise

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate estimated cost in USD.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Cost in USD
        """
        pricing = self.MODEL_PRICING.get(
            self.model_name,
            {"input": 0.50, "output": 3.00}  # Default to Flash pricing
        )

        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def test_connection(self) -> bool:
        """
        Test Gemini API connection with a minimal request.

        Returns:
            True if connection successful

        Raises:
            GeminiAPIError: If connection fails
        """
        try:
            logger.info("Testing Gemini API connection...")
            response = self.generate_text(
                system_prompt="You are a helpful assistant.",
                user_prompt="Say 'ok' if you can read this.",
                max_tokens=10
            )

            if response["content"]:
                logger.info(f"✓ Gemini API connection successful (model: {self.model_name})")
                return True
            else:
                raise GeminiAPIError("Empty response from Gemini API")

        except Exception as e:
            logger.error(f"✗ Gemini API connection failed: {e}")
            raise GeminiAPIError(f"Connection test failed: {e}")
