"""Read-only selected-provider model check; never generates a response."""

import asyncio

from openai import AsyncOpenAI

from cyberpath_ai.ai.providers import (
    OPENROUTER_BASE_URL, ProviderAdapter, ProviderConfigurationError,
    ProviderSelection, classify_provider_error,
)


async def check_model() -> int:
    try:
        selection = ProviderSelection.from_env()
    except ProviderConfigurationError as error:
        print(f"Configuration error: {error}")
        return 2
    print(f"Selected provider: {selection.name}")
    if selection.api_key and selection.api_key in selection.model:
        print("Configured model: <redacted because it contains the API key>")
        print("Credentials present: yes")
        print("Model available: not checked")
        print("Compatibility issue: model configuration contains a credential.")
        return 2
    print(f"Configured model: {selection.model}")
    print(f"Credentials present: {'yes' if selection.api_key else 'no'}")
    if not selection.api_key:
        print("Model available: not checked")
        print(f"Compatibility issue: {selection.key_variable} is not set.")
        return 2
    options = {"api_key": selection.api_key, "timeout": 15, "max_retries": 0}
    if selection.name == "openrouter":
        options["base_url"] = OPENROUTER_BASE_URL
    try:
        async with AsyncOpenAI(**options) as client:
            probe = await ProviderAdapter(selection, 15, client).check_model()
    except Exception as error:
        failure = classify_provider_error(error, selection, during_check=True)
        print("Model available: not confirmed")
        print(f"Failure category: {failure.category}")
        print(f"Details: {failure.message}")
        return 1
    if probe.virtual_router:
        print("Model available: virtual router; checked when generating")
    else:
        print(f"Model available: {'yes' if probe.available else 'no'}")
    print(f"Compatibility issue: {probe.compatibility_issue or 'none detected by metadata check'}")
    print("No generation request was made.")
    return 0 if probe.available and not probe.compatibility_issue else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check_model()))
