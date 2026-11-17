"""Typed gateway errors (System Design 3.1).

The agent layer catches these to degrade honestly instead of hallucinating
tool results or surfacing raw transport failures.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for every model-gateway failure."""


class GatewayConfigError(GatewayError):
    """The configured provider is not usable (e.g. missing API key)."""


class ProviderHTTPError(GatewayError):
    """The provider answered with an HTTP error status.

    ``body_snippet`` carries the first bytes of the error response so the
    failure is diagnosable without re-dialing the provider.
    """


class ToolCallDegradationError(GatewayError):
    """The model repeatedly failed to emit valid tool calls.

    An *attempt* is one ``ChatClient.stream`` call with tools attached. An
    attempt fails when it yields no valid tool call — malformed tool JSON
    (unparseable arguments or missing function name) or no tool call at all.
    After ``N`` consecutive failed attempts (``LLM_TOOL_DEGRADATION_ATTEMPTS``),
    this error is raised at the end of that stream's events. Any attempt with
    a valid tool call resets the counter, so a model that can run tools is
    never latched as degraded. Callers must degrade honestly — never
    fabricate tool results.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        failed_attempts: int,
        attempt_limit: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.failed_attempts = failed_attempts
        self.attempt_limit = attempt_limit
        super().__init__(
            f"Model '{model}' ({provider}) produced no valid tool calls on "
            f"{failed_attempts} consecutive tool-enabled attempts "
            f"(limit {attempt_limit}). This model can't reliably run the "
            "shop tools — try a tool-capable model (e.g. qwen3:8b on "
            "Ollama) or switch LLM_PROVIDER."
        )
