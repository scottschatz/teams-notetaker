"""
Claude API Client

Wrapper around Anthropic's Python SDK for generating meeting summaries.
Handles API authentication, token counting, and error handling.
"""

import logging
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
import anthropic
from anthropic import Anthropic, APIError, RateLimitError as AnthropicRateLimitError

from ..core.config import ClaudeConfig
from ..core.exceptions import ClaudeAPIError, RateLimitError


logger = logging.getLogger(__name__)


class ClaudeClient:
    """
    Claude API client for generating meeting summaries.

    Wraps Anthropic SDK with:
    - Token tracking (input/output)
    - Rate limit handling
    - Streaming support
    - Cost estimation

    Usage:
        config = ClaudeConfig(api_key='sk-ant-...', model='claude-sonnet-4-20250514')
        client = ClaudeClient(config)
        response = client.generate_text(
            system_prompt="You are a meeting summarizer",
            user_prompt="Summarize this meeting...",
            max_tokens=2000
        )
    """

    # Model pricing (per million tokens) - as of Dec 2025
    # Source: https://docs.anthropic.com/en/docs/about-claude/models
    MODEL_PRICING = {
        # Claude 4 models (short aliases used in config)
        "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
        "claude-opus-4-5": {"input": 15.00, "output": 75.00},
        # Claude 4 models (full IDs with dates)
        "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
        "claude-opus-4-5-20251101": {"input": 15.00, "output": 75.00},
        # Claude 3.5 models
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    }

    def __init__(self, config: ClaudeConfig):
        """
        Initialize Claude API client.

        Args:
            config: ClaudeConfig with API key and model settings
        """
        self.config = config
        self._client = Anthropic(api_key=config.api_key)

        self.max_retries = 3  # Max retry attempts for transient errors
        logger.info(f"ClaudeClient initialized (model: {config.model})")

    # Threshold for using streaming (requests that may take >10 minutes)
    STREAMING_THRESHOLD = 16000  # Use streaming for max_tokens >= this

    def _make_api_call_with_retry(
        self,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str,
        messages: List[Dict],
        stop_sequences: Optional[List[str]] = None,
        retry_count: int = 0
    ):
        """
        Make Claude API call with retry logic for transient errors.

        Uses streaming for large requests (max_tokens >= 16000) to avoid
        timeout issues on long-running operations.

        Handles:
        - Rate limiting (429): Wait based on retry-after or exponential backoff
        - Server errors (5xx): Retry with exponential backoff
        - Overloaded errors: Retry with backoff

        Args:
            model: Model to use
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: System prompt
            messages: Messages array
            stop_sequences: Optional stop sequences
            retry_count: Current retry attempt (internal)

        Returns:
            API response object

        Raises:
            RateLimitError: If rate limited and max retries exceeded
            ClaudeAPIError: If API error and max retries exceeded
        """
        try:
            # Use streaming for large requests to avoid 10-minute timeout
            use_streaming = max_tokens >= self.STREAMING_THRESHOLD

            if use_streaming:
                logger.debug(f"Using streaming for large request (max_tokens={max_tokens})")
                # Use streaming API and collect the full response
                with self._client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=messages,
                    stop_sequences=stop_sequences
                ) as stream:
                    # Get the final message which contains full response and usage stats
                    return stream.get_final_message()
            else:
                # Standard non-streaming call for smaller requests
                return self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=messages,
                    stop_sequences=stop_sequences
                )

        except AnthropicRateLimitError as e:
            # Rate limited - retry with backoff
            if retry_count < self.max_retries:
                # Try to get retry-after from headers, default to exponential backoff
                wait_time = min(2 ** retry_count * 10, 60)  # 10s, 20s, 40s, max 60s
                logger.warning(
                    f"Claude API rate limited, waiting {wait_time}s before retry "
                    f"{retry_count + 1}/{self.max_retries}: {e}"
                )
                time.sleep(wait_time)
                return self._make_api_call_with_retry(
                    model, max_tokens, temperature, system, messages,
                    stop_sequences, retry_count + 1
                )
            else:
                logger.error(f"Claude API rate limit exceeded after {self.max_retries} retries")
                raise RateLimitError(f"Claude API rate limited after {self.max_retries} retries: {e}")

        except APIError as e:
            # Check if it's a retryable server error (5xx or overloaded)
            is_server_error = hasattr(e, 'status_code') and e.status_code >= 500
            is_overloaded = 'overloaded' in str(e).lower()

            if (is_server_error or is_overloaded) and retry_count < self.max_retries:
                wait_time = min(2 ** retry_count * 5, 30)  # 5s, 10s, 20s, max 30s
                logger.warning(
                    f"Claude API server error, waiting {wait_time}s before retry "
                    f"{retry_count + 1}/{self.max_retries}: {e}"
                )
                time.sleep(wait_time)
                return self._make_api_call_with_retry(
                    model, max_tokens, temperature, system, messages,
                    stop_sequences, retry_count + 1
                )
            else:
                logger.error(f"Claude API error: {e}", exc_info=True)
                raise ClaudeAPIError(f"Claude API request failed: {e}")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
        cache_prefix: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate text completion using Claude API with optional prompt caching.

        Args:
            system_prompt: System prompt (role/instructions)
            user_prompt: User prompt (content to process)
            max_tokens: Maximum tokens to generate (default from config)
            temperature: Sampling temperature (0.0-1.0, default 1.0)
            stop_sequences: Optional stop sequences
            cache_prefix: Optional prefix to cache (e.g., transcript). If provided,
                         user_prompt will be split into: [cache_prefix] + [remaining].
                         The cache_prefix will be marked for caching (5min TTL).
                         Saves 90% on input costs for subsequent calls.

        Returns:
            Dictionary with:
                - content: Generated text
                - input_tokens: Number of input tokens
                - output_tokens: Number of output tokens
                - total_tokens: Total tokens used
                - model: Model used
                - cost: Estimated cost in USD (accounts for cache savings)
                - stop_reason: Why generation stopped
                - cache_creation_tokens: Tokens written to cache (first call)
                - cache_read_tokens: Tokens read from cache (subsequent calls)

        Raises:
            ClaudeAPIError: If API request fails
            RateLimitError: If rate limited
        """
        try:
            # Use max_tokens from config if not specified
            if max_tokens is None:
                max_tokens = self.config.max_tokens

            # Build messages array with optional caching
            if cache_prefix:
                # Split prompt: cacheable prefix + dynamic suffix
                # The prefix (transcript) gets cached, suffix (instructions) doesn't
                suffix = user_prompt.replace(cache_prefix, "", 1).strip()

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": cache_prefix,
                                "cache_control": {"type": "ephemeral"}  # Cache for 5 min
                            },
                            {
                                "type": "text",
                                "text": suffix
                            }
                        ]
                    }
                ]
                logger.info(f"Generating with PROMPT CACHING (prefix: {len(cache_prefix)} chars)")
            else:
                # No caching - simple string message
                messages = [{"role": "user", "content": user_prompt}]
                logger.info(f"Generating text with {self.config.model} (max_tokens: {max_tokens}, temp: {temperature})")

            # Make API request with retry logic for transient errors
            start_time = datetime.now()
            response = self._make_api_call_with_retry(
                model=self.config.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=messages,
                stop_sequences=stop_sequences
            )
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Extract response data
            content = response.content[0].text if response.content else ""
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            total_tokens = input_tokens + output_tokens
            stop_reason = response.stop_reason

            # Extract cache usage stats (if available)
            cache_creation_tokens = getattr(response.usage, 'cache_creation_input_tokens', 0)
            cache_read_tokens = getattr(response.usage, 'cache_read_input_tokens', 0)

            # Calculate cost (accounting for cache savings)
            cost = self._calculate_cost(
                input_tokens,
                output_tokens,
                self.config.model,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens
            )

            # Log with cache stats if applicable
            if cache_read_tokens > 0:
                logger.info(
                    f"✓ Generated {output_tokens} tokens in {duration_ms}ms "
                    f"(input: {input_tokens}, cached: {cache_read_tokens}, "
                    f"cost: ${cost:.4f}, stop: {stop_reason}) [CACHE HIT]"
                )
            elif cache_creation_tokens > 0:
                logger.info(
                    f"✓ Generated {output_tokens} tokens in {duration_ms}ms "
                    f"(input: {input_tokens}, cached: {cache_creation_tokens}, "
                    f"cost: ${cost:.4f}, stop: {stop_reason}) [CACHE WRITE]"
                )
            else:
                logger.info(
                    f"✓ Generated {output_tokens} tokens in {duration_ms}ms "
                    f"(input: {input_tokens}, total: {total_tokens}, cost: ${cost:.4f}, stop: {stop_reason})"
                )

            return {
                "content": content,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "model": self.config.model,
                "cost": cost,
                "stop_reason": stop_reason,
                "generation_time_ms": duration_ms,
                "cache_creation_tokens": cache_creation_tokens,
                "cache_read_tokens": cache_read_tokens
            }

        except (RateLimitError, ClaudeAPIError):
            # Re-raise errors from retry helper
            raise

        except Exception as e:
            logger.error(f"Unexpected error calling Claude API: {e}", exc_info=True)
            raise ClaudeAPIError(f"Unexpected error: {e}")

    def generate_with_streaming(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Generate text with streaming (for real-time UI updates).

        Args:
            system_prompt: System prompt
            user_prompt: User prompt
            max_tokens: Maximum tokens (default from config)
            temperature: Sampling temperature
            callback: Optional callback function(chunk: str) called for each chunk

        Returns:
            Same format as generate_text()
        """
        try:
            if max_tokens is None:
                max_tokens = self.config.max_tokens

            logger.info(f"Generating text with streaming (max_tokens: {max_tokens})")

            content_chunks = []
            start_time = datetime.now()

            # Stream response
            with self._client.messages.stream(
                model=self.config.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            ) as stream:
                for text in stream.text_stream:
                    content_chunks.append(text)
                    if callback:
                        callback(text)

                # Get final message with usage stats
                final_message = stream.get_final_message()

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Combine chunks
            content = "".join(content_chunks)
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens
            total_tokens = input_tokens + output_tokens
            stop_reason = final_message.stop_reason

            cost = self._calculate_cost(input_tokens, output_tokens, self.config.model)

            logger.info(
                f"✓ Streamed {output_tokens} tokens in {duration_ms}ms "
                f"(input: {input_tokens}, cost: ${cost:.4f})"
            )

            return {
                "content": content,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "model": self.config.model,
                "cost": cost,
                "stop_reason": stop_reason,
                "generation_time_ms": duration_ms
            }

        except AnthropicRateLimitError as e:
            logger.error(f"Claude API rate limit exceeded: {e}")
            raise RateLimitError(f"Claude API rate limited: {e}")

        except APIError as e:
            logger.error(f"Claude API error: {e}", exc_info=True)
            raise ClaudeAPIError(f"Claude API request failed: {e}")

        except Exception as e:
            logger.error(f"Unexpected error in streaming: {e}", exc_info=True)
            raise ClaudeAPIError(f"Streaming error: {e}")

    def _calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0
    ) -> float:
        """
        Calculate estimated cost in USD, accounting for prompt caching.

        Args:
            input_tokens: Number of regular input tokens
            output_tokens: Number of output tokens
            model: Model name
            cache_creation_tokens: Tokens written to cache (same price as input)
            cache_read_tokens: Tokens read from cache (90% discount)

        Returns:
            Cost in USD

        Notes:
            Prompt caching pricing (as of Dec 2025):
            - Cache writes: Same as regular input ($3.00/MTok for Sonnet 4.5)
            - Cache reads: 90% discount ($0.30/MTok for Sonnet 4.5)
            - Cache TTL: 5 minutes
        """
        # Try exact match first
        pricing = self.MODEL_PRICING.get(model)

        # If not found, try partial matching (e.g., "claude-haiku-4-5" matches "haiku")
        if not pricing:
            model_lower = model.lower()
            if "haiku" in model_lower:
                pricing = {"input": 1.00, "output": 5.00}
            elif "opus" in model_lower:
                pricing = {"input": 15.00, "output": 75.00}
            elif "sonnet" in model_lower:
                pricing = {"input": 3.00, "output": 15.00}
            else:
                # Default to Haiku pricing (cheapest) if unknown
                logger.warning(f"Unknown model '{model}' for pricing, defaulting to Haiku rates")
                pricing = {"input": 1.00, "output": 5.00}

        # Regular input tokens
        regular_input_cost = (input_tokens / 1_000_000) * pricing["input"]

        # Cache creation (writes) - same price as input
        cache_write_cost = (cache_creation_tokens / 1_000_000) * pricing["input"]

        # Cache reads - 90% discount (10% of normal price)
        cache_read_cost = (cache_read_tokens / 1_000_000) * (pricing["input"] * 0.10)

        # Output tokens - always full price
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return regular_input_cost + cache_write_cost + cache_read_cost + output_cost

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text using Anthropic's token counter.

        Args:
            text: Text to count tokens for

        Returns:
            Number of tokens
        """
        try:
            # Anthropic SDK provides a count_tokens method
            response = self._client.messages.count_tokens(
                model=self.config.model,
                messages=[{"role": "user", "content": text}]
            )
            return response.input_tokens
        except:
            # Fallback: rough estimate (4 chars per token)
            return len(text) // 4

    def test_connection(self) -> bool:
        """
        Test Claude API connection with a minimal request.

        Returns:
            True if connection successful

        Raises:
            ClaudeAPIError: If connection fails
        """
        try:
            logger.info("Testing Claude API connection...")
            response = self.generate_text(
                system_prompt="You are a helpful assistant.",
                user_prompt="Say 'ok' if you can read this.",
                max_tokens=10
            )

            if response["content"]:
                logger.info(f"✓ Claude API connection successful (model: {self.config.model})")
                return True
            else:
                raise ClaudeAPIError("Empty response from Claude API")

        except Exception as e:
            logger.error(f"✗ Claude API connection failed: {e}")
            raise ClaudeAPIError(f"Connection test failed: {e}")

    def estimate_cost(self, input_text: str, expected_output_tokens: int = 1000) -> float:
        """
        Estimate cost for a given input and expected output length.

        Args:
            input_text: Input text
            expected_output_tokens: Expected output length in tokens

        Returns:
            Estimated cost in USD
        """
        input_tokens = self.count_tokens(input_text)
        return self._calculate_cost(input_tokens, expected_output_tokens, self.config.model)
