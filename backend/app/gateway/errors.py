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

    Raised after ``N`` consecutive tool-enabled attempts produced no valid
    tool call (malformed JSON arguments, missing name, or no tool call at
    all). Callers must treat this as "this model can't run the tools" and
    degrade honestly — never fabricate tool results.
    """
