"""Teste pela pilha HTTP real (requests) contra um servidor local que imita o OpenRouter."""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests

from citizentech_ai.ai import AIService
from citizentech_ai.config import LLMConfig
from citizentech_ai.llm_client import OpenRouterClient
from citizentech_ai.mock_backend import MockBackend
from citizentech_ai.pipeline import run_pipeline
from tests.conftest import TZ

DATA = Path(__file__).resolve().parent.parent / "data"
SEEN = []


class FakeOpenRouter(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        system = body["messages"][0]["content"]
        task = "analyze_report" if "triagem de ocorrências" in system else "same_occurrence" if "duplicatas" in system else "summarize_cluster"
        content = json.dumps(MockBackend()._answer(body["messages"], task), ensure_ascii=False)
        out = json.dumps({"model": body["model"], "choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def server():
    SEEN.clear()
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/api/v1"
    srv.shutdown()


def test_full_pipeline_over_real_http(server):
    session = requests.Session()
    session.trust_env = False  # ignora proxy do ambiente para falar com 127.0.0.1
    client = OpenRouterClient(LLMConfig(api_key="test-key", base_url=server, model="vendor/model-x"), session=session)
    res = run_pipeline(DATA, AIService(client), datetime(2026, 11, 9, tzinfo=TZ))
    assert res.queue[0].category == "alagamento" and res.stats["analysis_sources"] == {"llm": 26}
    assert len(SEEN) == 26 + 1 + len(res.queue)  # 26 classificações + 1 desempate + 1 resumo por ocorrência
    first = SEEN[0]
    assert first["path"].endswith("/chat/completions") and first["auth"] == "Bearer test-key"
    assert first["body"]["model"] == "vendor/model-x"
    rf = first["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
