"""
Claude API Client

Wrapper around Anthropic's Python SDK for generating meeting summaries.
Handles API authentication, token counting, and error handling.
"""

import logging
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

    # Model pricing (per million tokens) - as of Jan 2025
    # Source: https://www.anthropic.com/api-pricing
    MODEL_PRICING = {
        "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-20250301": {"input": 0.80, "output": 4.00},
    }

    def __init__(self, config: ClaudeConfig):
        """
        Initialize Claude API client.

        Args:
            config: ClaudeConfig with API key and model settings
        """
        self.config = config
        self._client = Anthropic(api_key=config.api_key)

        logger.info(f"ClaudeClient initialized (model: {config.model})")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Generate text completion using Claude API.

        Args:
            system_prompt: System prompt (role/instructions)
            user_prompt: User prompt (content to process)
            max_tokens: Maximum tokens to generate (default from config)
            temperature: Sampling temperature (0.0-1.0, default 1.0)
            stop_sequences: Optional stop sequences

        Returns:
            Dictionary with:
                - content: Generated text
                - input_tokens: Number of input tokens
                - output_tokens: Number of output tokens
                - total_tokens: Total tokens used
                - model: Model used
                - cost: Estimated cost in USD
                - stop_reason: Why generation stopped

        Raises:
            ClaudeAPIError: If API request fails
            RateLimitError: If rate limited
        """
        try:
            # Use max_tokens from config if not specified
            if max_tokens is None:
                max_tokens = self.config.max_tokens

            logger.info(f"Generating text with {self.config.model} (max_tokens: {max_tokens}, temp: {temperature})")

            # Make API request
            start_time = datetime.now()
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
                stop_sequences=stop_sequences
            )
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Extract response data
            content = response.content[0].text if response.content else ""
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            total_tokens = input_tokens + output_tokens
            stop_reason = response.stop_reason

            # Calculate cost
            cost = self._calculate_cost(input_tokens, output_tokens, self.config.model)

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
                "generation_time_ms": duration_ms
            }

        except AnthropicRateLimitError as e:
            logger.error(f"Claude API rate limit exceeded: {e}")
            raise RateLimitError(f"Claude API rate limited: {e}")

        except APIError as e:
            logger.error(f"Claude API error: {e}", exc_info=True)
            raise ClaudeAPIError(f"Claude API request failed: {e}")

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

    def _calculate_cost(self, input_tokens: int, output_tokens: int, model: str) -> float:
        """
        Calculate estimated cost in USD.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            model: Model name

        Returns:
            Cost in USD
        """
        pricing = self.MODEL_PRICING.get(model, {"input": 3.00, "output": 15.00})
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

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
