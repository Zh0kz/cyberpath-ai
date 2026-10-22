"""Provider-specific SDK calls; no Telegram or news storage concerns here."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
from typing import Mapping, TYPE_CHECKING
from urllib.parse import quote

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from cyberpath_ai.config import Settings


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ProviderConfigurationError(Exception):
    """Selected provider cannot be used with current configuration."""


@dataclass(frozen=True)
class ProviderSelection:
    name: str
    model: str
    api_key: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProviderSelection":
        values = os.environ if environ is None else environ
        name = values.get("AI_PROVIDER", "openrouter").strip().lower()
        if name not in {"openrouter", "openai"}:
            raise ProviderConfigurationError("AI_PROVIDER must be openrouter or openai.")
        model_var, key_var, default_model = (
            ("OPENROUTER_MODEL", "OPENROUTER_API_KEY", "openrouter/free")
            if name == "openrouter" else
            ("OPENAI_MODEL", "OPENAI_API_KEY", "gpt-6-luna")
        )
        model = values.get(model_var, default_model).strip()
        if not model:
            raise ProviderConfigurationError(f"{model_var} must not be empty.")
        return cls(name, model, values.get(key_var, "").strip())

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ProviderSelection":
        if settings.ai_provider == "openrouter":
            return cls("openrouter", settings.openrouter_model, settings.openrouter_api_key)
        if settings.ai_provider == "openai":
            return cls("openai", settings.openai_model, settings.openai_api_key)
        raise ProviderConfigurationError("AI_PROVIDER must be openrouter or openai.")

    @property
    def cache_key(self) -> str:
        return f"{self.name}:{self.model}"

    @property
    def key_variable(self) -> str:
        return "OPENROUTER_API_KEY" if self.name == "openrouter" else "OPENAI_API_KEY"

    def require_key(self) -> None:
        if not self.api_key:
            raise ProviderConfigurationError(
                f"AI is not configured for {self.name}. Set {self.key_variable}."
            )


@dataclass(frozen=True)
class ModelProbe:
    available: bool
    compatibility_issue: str | None = None
    virtual_router: bool = False


@dataclass(frozen=True)
class ProviderFailure:
    category: str
    message: str


def classify_provider_error(error: Exception, selection: ProviderSelection,
                            *, during_check: bool) -> ProviderFailure:
    """Keep SDK exception text out of user-visible diagnostics."""
    kind = type(error).__name__
    if kind == "AuthenticationError":
        return ProviderFailure("authentication", f"Authentication failed. Check {selection.key_variable}.")
    if kind == "PermissionDeniedError":
        if during_check and selection.name == "openrouter" and selection.model == "openrouter/free":
            return ProviderFailure("authentication", "OpenRouter rejected this API key.")
        return ProviderFailure("model_access", "This API key lacks access to the configured model.")
    if kind == "NotFoundError":
        if during_check and selection.name == "openrouter" and selection.model == "openrouter/free":
            return ProviderFailure("compatibility", "OpenRouter's key-check endpoint is unavailable.")
        return ProviderFailure("model_access", "The configured model was not found by the provider.")
    if kind in {"APITimeoutError", "TimeoutError"}:
        return ProviderFailure("timeout", "The AI provider timed out. Please try again later.")
    if kind == "APIConnectionError":
        return ProviderFailure("connection", "Could not connect to the AI provider. Please try again later.")
    if kind == "RateLimitError":
        return ProviderFailure("rate_limit", "AI rate limit or free quota reached. Please try again later.")
    if kind == "BadRequestError":
        return ProviderFailure("compatibility", "The provider rejected this model or request format.")
    if isinstance(error, (ValueError, TypeError)):
        subject = "provider check" if during_check else "AI provider"
        return ProviderFailure("invalid_response", f"The {subject} returned an unreadable response.")
    if during_check:
        return ProviderFailure("provider_error", "Could not check model access. Please try again later.")
    return ProviderFailure("provider_error", "The AI explanation is temporarily unavailable. Please try again later.")


def _free_pricing_confirmed(model: dict[str, object]) -> bool:
    prices = model.get("pricing")
    if not isinstance(prices, dict):
        return False
    try:
        return all(Decimal(str(prices[key])) == 0 for key in ("prompt", "completion", "request"))
    except (KeyError, InvalidOperation, TypeError):
        return False


class ProviderAdapter:
    def __init__(self, selection: ProviderSelection, timeout: int,
                 client: object | None = None) -> None:
        self.selection = selection
        self.timeout = timeout
        self._client = client

    def client(self) -> object:
        self.selection.require_key()
        if self._client is None:
            args = {
                "api_key": self.selection.api_key,
                "timeout": self.timeout,
                "max_retries": 0,
            }
            if self.selection.name == "openrouter":
                args["base_url"] = OPENROUTER_BASE_URL
            self._client = AsyncOpenAI(**args)
        return self._client

    async def check_model(self) -> ModelProbe:
        client = self.client()
        if self.selection.name == "openai":
            await client.models.retrieve(self.selection.model)
            return ModelProbe(True)

        if self.selection.model == "openrouter/free":
            # This is a dynamic free router, not a fixed model slug. A model
            # metadata lookup may omit it even when Chat Completions works.
            # Verify the key; actual route availability is tested by generation.
            await client.get("key", cast_to=dict[str, object])
            return ModelProbe(True, virtual_router=True)

        # OpenRouter's model lookup is /model/{author}/{slug}, not the OpenAI
        # SDK's /models/{id}. Use the SDK's generic read-only GET method.
        path = "model/" + quote(self.selection.model, safe="/:-_")
        payload = await client.get(path, cast_to=dict[str, object])
        model = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(model, dict) or model.get("id") != self.selection.model:
            return ModelProbe(False, "OpenRouter returned invalid model metadata.")
        architecture = model.get("architecture")
        if isinstance(architecture, dict):
            outputs = architecture.get("output_modalities")
            if isinstance(outputs, list) and "text" not in outputs:
                return ModelProbe(True, "The selected model has no text output.")
        if self.selection.model.endswith(":free") and not _free_pricing_confirmed(model):
            return ModelProbe(True, "Free pricing could not be confirmed; generation is blocked.")
        return ModelProbe(True)

    async def generate(self, instructions: str, article_input: str,
                       schema: dict[str, object], max_output_tokens: int) -> object:
        client = self.client()
        if self.selection.name == "openai":
            return await client.responses.create(
                model=self.selection.model,
                instructions=instructions,
                input=article_input,
                text={"format": {"type": "json_schema", "name": "cyber_news_lesson",
                                 "schema": schema, "strict": True}},
                reasoning={"effort": "none"},
                max_output_tokens=max_output_tokens,
                tools=[], tool_choice="none", store=False,
            )
        # The virtual free route may select a model without response_format
        # support. The prompt requests delimited text; local validation also
        # accepts JSON if the model returns it. No extra generation call occurs.
        return await client.chat.completions.create(
            model=self.selection.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": article_input},
            ],
            max_tokens=max_output_tokens,
            n=1,
            stream=False,
        )
