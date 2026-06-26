from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from skill_app.domain.errors import (
    ConfigurationError,
    ModelProviderError,
    ModelTimeoutError,
)
from skill_app.schemas.model import ModelMessage, ModelProfile
from skill_app.services import model_providers
from skill_app.services.model_providers import FakeModelProvider, OpenAICompatibleProvider


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, response: FakeHttpResponse) -> None:
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


def profile(**overrides) -> ModelProfile:
    values = {
        "name": "test-http",
        "provider": "openai_compatible",
        "model": "demo",
        "base_url": "http://model.local/v1",
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return ModelProfile(**values)


def test_fake_provider_invokes_and_streams():
    provider = FakeModelProvider()
    messages = [ModelMessage(role="user", content="echo")]
    response = provider.invoke(profile(provider="fake"), messages)
    assert response.text == "echo"
    assert list(provider.stream(profile(provider="fake"), messages)) == ["echo"]


def test_openai_compatible_provider_parses_response(monkeypatch):
    monkeypatch.setattr(
        model_providers,
        "urlopen",
        lambda *_args, **_kwargs: FakeHttpResponse(
            {
                "id": "call-1",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        ),
    )
    response = OpenAICompatibleProvider().invoke(
        profile(),
        [ModelMessage(role="user", content="hi")],
    )
    assert response.text == "hello"
    assert response.usage.input_tokens == 3
    assert response.provider_metadata["request_id"] == "call-1"
    assert list(
        OpenAICompatibleProvider().stream(
            profile(),
            [ModelMessage(role="user", content="hi")],
        )
    ) == ["hello"]


def test_openai_compatible_provider_bypasses_proxy_for_localhost(monkeypatch):
    response = FakeHttpResponse(
        {
            "id": "local-call",
            "choices": [{"message": {"content": "local"}}],
        }
    )
    opener = FakeOpener(response)
    monkeypatch.setattr(model_providers, "build_opener", lambda *_args: opener)
    monkeypatch.setattr(
        model_providers,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("localhost request used environment proxy"),
    )

    result = OpenAICompatibleProvider().invoke(
        profile(base_url="http://127.0.0.1:11434/v1"),
        [ModelMessage(role="user", content="hi")],
    )

    assert result.text == "local"
    assert len(opener.requests) == 1


def test_openai_compatible_provider_sends_schema_and_disables_reasoning(monkeypatch):
    response = FakeHttpResponse(
        {
            "id": "structured-call",
            "choices": [{"message": {"content": '{"value":"ok"}'}}],
        }
    )
    opener = FakeOpener(response)
    monkeypatch.setattr(model_providers, "build_opener", lambda *_args: opener)
    provider = OpenAICompatibleProvider()

    result = provider.invoke_structured(
        profile(
            base_url="http://localhost:11434/v1",
            supports_structured_outputs=True,
            reasoning_effort="none",
            max_output_tokens=4096,
        ),
        [ModelMessage(role="user", content="return a value")],
        response_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        schema_name="DemoOutput",
    )

    body = json.loads(opener.requests[0][0].data)
    assert result.text == '{"value":"ok"}'
    assert body["reasoning_effort"] == "none"
    assert body["max_tokens"] == 4096
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "DemoOutput"
    assert body["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), ModelTimeoutError),
        (HTTPError("http://model", 500, "error", {}, None), ModelProviderError),
        (URLError("offline"), ModelProviderError),
    ],
)
def test_openai_compatible_provider_normalizes_transport_errors(monkeypatch, error, expected):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(model_providers, "urlopen", fail)
    with pytest.raises(expected):
        OpenAICompatibleProvider().invoke(
            profile(),
            [ModelMessage(role="user", content="hi")],
        )


def test_openai_compatible_provider_rejects_missing_url_and_invalid_body(monkeypatch):
    with pytest.raises(ConfigurationError):
        OpenAICompatibleProvider().invoke(
            profile(base_url=None),
            [ModelMessage(role="user", content="hi")],
        )

    monkeypatch.setattr(
        model_providers,
        "urlopen",
        lambda *_args, **_kwargs: FakeHttpResponse({"choices": []}),
    )
    with pytest.raises(ModelProviderError):
        OpenAICompatibleProvider().invoke(
            profile(),
            [ModelMessage(role="user", content="hi")],
        )
