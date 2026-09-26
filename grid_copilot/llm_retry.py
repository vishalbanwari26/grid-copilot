"""Retry a hosted model on rate limits and on malformed JSON.

Groq's free tier allows a few thousand tokens per minute, and one investigation
makes ten or more calls, so a live run hits the limit routinely. This wraps any
`LLMClient` and, on a rate-limit error, waits (using the delay the provider
suggests when it gives one) and tries again. When the provider rejects the
model's own output as invalid JSON (Groq returns `json_validate_failed`, seen in
practice as `"confidence": 0. nine`), the same request is simply sent again, a
few times at most. Any other error is raised at once.
"""

from __future__ import annotations

import re
import time

from cortex.llm import ImageInput, LLMClient, LLMResponse


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return "RateLimit" in text or "429" in text or "rate_limit" in text


def _is_bad_json(exc: Exception) -> bool:
    return "json_validate_failed" in str(exc)


def _suggested_wait(exc: Exception) -> float | None:
    m = re.search(r"try again in ([\d.]+)(ms|s)", str(exc))
    if not m:
        return None
    value = float(m.group(1))
    return value / 1000 if m.group(2) == "ms" else value


class RetryingClient(LLMClient):
    def __init__(self, inner: LLMClient, max_retries: int = 8, base_wait: float = 4.0) -> None:
        self.inner = inner
        self.max_retries = max_retries
        self.base_wait = base_wait
        self.waited_s = 0.0
        self.json_retries = 0

    def complete(self, system: str, prompt: str, image: ImageInput | None = None) -> LLMResponse:
        for attempt in range(self.max_retries + 1):
            try:
                return self.inner.complete(system, prompt, image)
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise their own types
                if attempt == self.max_retries:
                    raise
                if _is_bad_json(exc) and self.json_retries < 3 * (attempt + 1):
                    self.json_retries += 1
                    continue
                if not _is_rate_limit(exc):
                    raise
                wait = max(_suggested_wait(exc) or 0.0, self.base_wait * (attempt + 1)) + 0.5
                self.waited_s += wait
                time.sleep(wait)
        raise RuntimeError("unreachable")
