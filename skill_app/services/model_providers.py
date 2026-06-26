from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from skill_app.domain.errors import ConfigurationError, ModelProviderError, ModelTimeoutError
from skill_app.schemas.model import ModelMessage, ModelProfile, ModelUsage, ProviderResponse


class FakeModelProvider:
    def invoke(self, profile: ModelProfile, messages: list[ModelMessage]) -> ProviderResponse:
        text = messages[-1].content
        return ProviderResponse(
            text=text,
            usage=ModelUsage(
                input_tokens=sum(len(message.content) for message in messages),
                output_tokens=len(text),
            ),
            provider_metadata={"fake": True},
        )

    def stream(self, profile: ModelProfile, messages: list[ModelMessage]) -> Iterator[str]:
        yield self.invoke(profile, messages).text


class OpenAICompatibleProvider:
    def invoke(self, profile: ModelProfile, messages: list[ModelMessage]) -> ProviderResponse:
        return self._invoke(profile, messages)

    def invoke_structured(
        self,
        profile: ModelProfile,
        messages: list[ModelMessage],
        *,
        response_schema: dict[str, Any],
        schema_name: str,
    ) -> ProviderResponse:
        response_format = None
        if profile.supports_structured_outputs:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        return self._invoke(
            profile,
            messages,
            response_format=response_format,
        )

    def _invoke(
        self,
        profile: ModelProfile,
        messages: list[ModelMessage],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        if not profile.base_url:
            raise ConfigurationError("OpenAI-compatible profile requires base_url.")
        request_body: dict[str, Any] = {
            "model": profile.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": profile.temperature,
            "stream": False,
        }
        if profile.reasoning_effort is not None:
            request_body["reasoning_effort"] = profile.reasoning_effort
        if profile.max_output_tokens is not None:
            request_body["max_tokens"] = profile.max_output_tokens
        if response_format is not None:
            request_body["response_format"] = response_format
        payload = json.dumps(request_body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if profile.api_key_env:
            api_key = os.getenv(profile.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        request = Request(
            f"{profile.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with _open_request(request, profile.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ModelTimeoutError(
                f"Model request timed out after {profile.timeout_seconds} seconds.",
                {"profile_name": profile.name},
            ) from exc
        except HTTPError as exc:
            raise ModelProviderError(
                "Model provider returned an HTTP error.",
                {"profile_name": profile.name, "status_code": exc.code},
            ) from exc
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            raise ModelProviderError(
                "Model provider request failed.",
                {"profile_name": profile.name, "error_type": type(exc).__name__},
            ) from exc
        try:
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return ProviderResponse(
                text=text,
                usage=ModelUsage(
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                ),
                provider_metadata={"request_id": body.get("id")},
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError(
                "Model provider returned an invalid response.",
                {"profile_name": profile.name},
            ) from exc

    def stream(self, profile: ModelProfile, messages: list[ModelMessage]) -> Iterator[str]:
        yield self.invoke(profile, messages).text


def _open_request(request: Request, timeout_seconds: int):
    hostname = urlparse(request.full_url).hostname
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        return build_opener(ProxyHandler({})).open(request, timeout=timeout_seconds)
    return urlopen(request, timeout=timeout_seconds)
