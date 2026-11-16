import pytest
import requests

from citizentech_ai.config import LLMConfig
from citizentech_ai.llm_client import LLMAuthError, LLMBadRequest, LLMUnavailable, OpenRouterClient
from tests.conftest import FakeResponse, FakeSession, ok_body

MSGS = [{"role": "user", "content": "oi"}]
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}


def client(script, **cfg):
    sleeps = []
    c = OpenRouterClient(LLMConfig(api_key="k", backoff_s=1.0, **cfg), session=FakeSession(script), sleep=sleeps.append)
    return c, sleeps


def test_success_sends_schema_and_headers():
    c, _ = client([FakeResponse(200, ok_body('{"a":"x"}'))])
    r = c.complete(MSGS, schema=SCHEMA, schema_name="t")
    call = c.session.calls[0]
    assert r.text == '{"a":"x"}' and r.prompt_tokens == 10 and r.model == "test/model"
    assert call["json"]["response_format"]["type"] == "json_schema"
    assert call["json"]["response_format"]["json_schema"]["strict"] is True
    assert call["json"]["temperature"] == 0.0
    assert call["headers"]["Authorization"] == "Bearer k" and "X-Title" in call["headers"]
    assert call["url"].endswith("/chat/completions")


def test_retries_429_with_backoff_then_succeeds():
    c, sleeps = client([FakeResponse(429, {"error": "rate"}), FakeResponse(503, {"error": "down"}), FakeResponse(200, ok_body("{}"))], max_retries=2)
    assert c.complete(MSGS).attempts == 3
    assert sleeps == [1.0, 2.0]


def test_respects_retry_after_header():
    c, sleeps = client([FakeResponse(429, {"error": "rate"}, headers={"Retry-After": "7"}), FakeResponse(200, ok_body("{}"))])
    c.complete(MSGS)
    assert sleeps == [7.0]


def test_auth_error_is_not_retried():
    c, sleeps = client([FakeResponse(401, {"error": "bad key"})])
    with pytest.raises(LLMAuthError):
        c.complete(MSGS)
    assert len(c.session.calls) == 1 and sleeps == []


def test_bad_request_raises_for_mode_downgrade():
    c, _ = client([FakeResponse(400, {"error": "response_format unsupported"})])
    with pytest.raises(LLMBadRequest):
        c.complete(MSGS, schema=SCHEMA)


def test_timeout_then_success():
    c, _ = client([requests.Timeout("slow"), FakeResponse(200, ok_body("{}"))])
    assert c.complete(MSGS).attempts == 2


def test_unavailable_after_retries_then_fallback_model_used():
    script = [FakeResponse(503, {"error": "x"})] * 3 + [FakeResponse(200, ok_body("{}", model="backup/model"))]
    c, _ = client(script, max_retries=2, fallback_models=("backup/model",))
    r = c.complete(MSGS)
    assert r.model == "backup/model"
    assert [x["json"]["model"] for x in c.session.calls] == ["openai/gpt-4o-mini"] * 3 + ["backup/model"]


def test_unavailable_when_all_models_fail():
    c, _ = client([FakeResponse(500, {"error": "x"})] * 3, max_retries=2)
    with pytest.raises(LLMUnavailable):
        c.complete(MSGS)


def test_http200_with_error_body_is_retried():
    c, _ = client([FakeResponse(200, {"error": {"code": 429, "message": "limit"}}), FakeResponse(200, ok_body("{}"))])
    assert c.complete(MSGS).attempts == 2


def test_empty_content_is_treated_as_failure():
    c, _ = client([FakeResponse(200, ok_body("   "))] * 3, max_retries=2)
    with pytest.raises(LLMUnavailable):
        c.complete(MSGS)


def test_missing_api_key():
    with pytest.raises(LLMAuthError):
        OpenRouterClient(LLMConfig(api_key=None))


def test_require_parameters_adds_provider_preference():
    c, _ = client([FakeResponse(200, ok_body("{}"))], require_parameters=True)
    c.complete(MSGS, schema=SCHEMA)
    assert c.session.calls[0]["json"]["provider"] == {"require_parameters": True}


def test_describe_never_leaks_key():
    d = LLMConfig(api_key="super-secret").describe()
    assert "super-secret" not in str(d) and d["api_key_set"] is True
