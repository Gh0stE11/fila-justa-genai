from datetime import datetime, timedelta, timezone

import pytest

from citizentech_ai.llm_client import LLMResponse
from citizentech_ai.models import Report

TZ = timezone(timedelta(hours=-3))


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text or (str(body) if body else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Substitui requests.Session: devolve respostas (ou levanta exceções) em sequência."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok_body(content, model="test/model"):
    return {"model": model, "choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class ScriptedBackend:
    """Backend de teste: cada item do script é texto (resposta) ou Exception (levantada)."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, *, schema=None, schema_name="response", response_mode="json_schema", task="generic"):
        self.calls.append({"messages": messages, "schema": schema, "mode": response_mode, "task": task})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model="scripted/model", latency_s=0.01, attempts=1)


def make_report(rid, lat, lon, text="texto", when=None, channel="app", address=None):
    return Report(rid, channel, when or datetime(2026, 11, 7, 12, 0, tzinfo=TZ), text, address, lat, lon, "gps" if lat is not None else "unresolved")


@pytest.fixture
def good_analysis_json():
    return (
        '{"category": "alagamento", "risk_to_life": false, "blocks_passage": true, "vulnerable_people": false,'
        ' "evidence": ["ninguém passa"], "confidence": 0.9, "summary": "Rua alagada"}'
    )
