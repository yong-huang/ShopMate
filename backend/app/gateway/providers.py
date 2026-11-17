"""Provider adapters: env-config-only endpoint resolution (System Design 3.1/3.9).

All three presets speak the OpenAI-compatible chat-completions surface, so an
adapter only resolves *where* to POST, *which* model to ask for, and *how* to
authenticate. Switching provider is an env change — never a code change.
"""

from pydantic import BaseModel

from app.config import Settings
from app.gateway.errors import GatewayConfigError

GLM_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class ResolvedEndpoint(BaseModel):
    """A concrete chat-completions endpoint derived from settings."""

    provider: str
    chat_url: str
    api_key: str | None
    model: str


class ProviderAdapter:
    """Resolves one provider preset from typed settings."""

    name: str

    def resolve(self, settings: Settings) -> ResolvedEndpoint:  # pragma: no cover - interface
        raise NotImplementedError


class GlmAdapter(ProviderAdapter):
    """GLM cloud preset (default): OpenAI-compatible BigModel endpoint."""

    name = "glm"

    def resolve(self, settings: Settings) -> ResolvedEndpoint:
        api_key = settings.llm_api_key
        if not api_key:
            raise GatewayConfigError(
                "LLM_API_KEY is required for the 'glm' provider; set it in "
                "backend/.env or the environment."
            )
        base_url = (settings.llm_base_url or GLM_DEFAULT_BASE_URL).rstrip("/")
        return ResolvedEndpoint(
            provider=self.name,
            chat_url=f"{base_url}/chat/completions",
            api_key=api_key,
            model=settings.llm_model,
        )


class OpenAIAdapter(ProviderAdapter):
    """OpenAI cloud preset (alternative cloud provider)."""

    name = "openai"

    def resolve(self, settings: Settings) -> ResolvedEndpoint:
        api_key = settings.llm_api_key
        if not api_key:
            raise GatewayConfigError(
                "LLM_API_KEY is required for the 'openai' provider; set it in "
                "backend/.env or the environment."
            )
        base_url = (settings.llm_base_url or OPENAI_DEFAULT_BASE_URL).rstrip("/")
        return ResolvedEndpoint(
            provider=self.name,
            chat_url=f"{base_url}/chat/completions",
            api_key=api_key,
            model=settings.llm_model,
        )


class OllamaAdapter(ProviderAdapter):
    """Ollama local preset: no API key, OpenAI-compatible surface under ``/v1``."""

    name = "ollama"

    def resolve(self, settings: Settings) -> ResolvedEndpoint:
        base_url = settings.ollama_base_url.rstrip("/")
        return ResolvedEndpoint(
            provider=self.name,
            chat_url=f"{base_url}/v1/chat/completions",
            api_key=None,
            model=settings.ollama_chat_model,
        )


ADAPTERS: dict[str, ProviderAdapter] = {
    GlmAdapter.name: GlmAdapter(),
    OpenAIAdapter.name: OpenAIAdapter(),
    OllamaAdapter.name: OllamaAdapter(),
}


def resolve_endpoint(settings: Settings) -> ResolvedEndpoint:
    """Select the adapter for ``LLM_PROVIDER`` and resolve its endpoint."""
    adapter = ADAPTERS[settings.llm_provider]
    return adapter.resolve(settings)
