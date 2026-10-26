"""Claude API access for watchd: key from the Keychain, one model, refusal handling.

The API key lives in the macOS Keychain (service "cmf-studio", account "anthropic-api-key"; set it with
`cmf set-key`). Without one, the SDK's own credential lookup (ANTHROPIC_API_KEY, `ant auth login`) applies.
Health data sent to Claude is aggregates only (docs/04-safety-policy.md).
"""

from __future__ import annotations

from typing import Any

import anthropic
import keyring

SERVICE = "cmf-studio"
ACCOUNT = "anthropic-api-key"
MODEL = "claude-opus-5-5"
# Server-side fallback: if a request is declined by a safety classifier, the API retries it on a
# suitable model within the same call (beta). Kept on by default.
BETAS = ["server-side-fallback-2026-07-01"]
FALLBACKS = "default"


class ClaudeUnavailable(RuntimeError):
    """No credentials configured."""


class ClaudeRefused(RuntimeError):
    pass


def get_key() -> str | None:
    return keyring.get_password(SERVICE, ACCOUNT)


def set_key(key: str) -> None:
    keyring.set_password(SERVICE, ACCOUNT, key)


def client() -> anthropic.AsyncAnthropic:
    key = get_key()
    try:
        return anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
    except anthropic.AnthropicError as exc:  # raised when no credential source resolves
        raise ClaudeUnavailable("no Anthropic API key: run `uv run cmf set-key`") from exc


async def parse(output_format: Any, *, system: str, user: str, effort: str = "medium", max_tokens: int = 16000) -> Any:
    """One structured-output request. Returns the validated pydantic object."""
    try:
        response = await client().beta.messages.parse(
            model=MODEL,
            max_tokens=max_tokens,
            betas=BETAS,
            fallbacks=FALLBACKS,
            output_config={"effort": effort},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
        )
    except anthropic.AuthenticationError as exc:
        raise ClaudeUnavailable("the Anthropic API key was rejected: run `uv run cmf set-key`") from exc
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None) if response.stop_details else None
        raise ClaudeRefused(f"Claude declined this request ({category or 'no category'})")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"Claude's answer was incomplete (stop reason {response.stop_reason})")
    return response.parsed_output
