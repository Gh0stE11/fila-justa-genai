"""Cliente do OpenRouter com retry, backoff e modelos de reserva."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import requests

from .config import LLMConfig


class LLMError(Exception):
    """Erro base do cliente."""


class LLMUnavailable(LLMError):
    """Rede, timeout, 429 ou 5xx mesmo após os retries e os modelos de reserva."""


class LLMAuthError(LLMError):
    """401/402/403: chave inválida, sem crédito ou sem permissão."""


class LLMBadRequest(LLMError):
    """400/404/422: em geral o modelo não aceita o response_format pedido."""


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1


class LLMBackend(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None = None,
        schema_name: str = "response",
        response_mode: str = "json_schema",
        task: str = "generic",
    ) -> LLMResponse: ...


class OpenRouterClient:
    name = "openrouter"

    def __init__(
        self,
        config: LLMConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not config.api_key:
            raise LLMAuthError("OPENROUTER_API_KEY não definida (veja .env.example).")
        self.config = config
        self.session = session or requests.Session()
        self._sleep = sleep

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None = None,
        schema_name: str = "response",
        response_mode: str = "json_schema",
        task: str = "generic",
    ) -> LLMResponse:
        last: LLMUnavailable | None = None
        for model in [self.config.model, *self.config.fallback_models]:
            try:
                return self._call_with_retries(model, messages, schema, schema_name, response_mode)
            except LLMUnavailable as exc:  # tenta o próximo modelo
                last = exc
        assert last is not None
        raise last

    def _payload(self, model, messages, schema, schema_name, response_mode) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if response_mode == "json_schema" and schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif response_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if self.config.require_parameters and "response_format" in body:
            body["provider"] = {"require_parameters": True}
        return body

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.config.app_url,  # atribuição opcional no OpenRouter
            "X-Title": self.config.app_title,
        }

    def _call_with_retries(self, model, messages, schema, schema_name, response_mode) -> LLMResponse:
        url = f"{self.config.base_url}/chat/completions"
        body = self._payload(model, messages, schema, schema_name, response_mode)
        last_error = "sem tentativas"
        for attempt in range(1, self.config.max_retries + 2):
            started = time.monotonic()
            retry_after: float | None = None
            try:
                resp = self.session.post(
                    url, headers=self._headers(), json=body, timeout=self.config.timeout_s
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                status = resp.status_code
                if status == 200:
                    parsed = self._parse_ok(resp, model, started, attempt)
                    if parsed is not None:
                        return parsed
                    last_error = "resposta vazia ou sem choices"
                elif status in (401, 402, 403):
                    raise LLMAuthError(f"HTTP {status}: {self._err_text(resp)}")
                elif status in (400, 404, 422):
                    raise LLMBadRequest(f"HTTP {status}: {self._err_text(resp)}")
                elif status == 429 or status >= 500:
                    last_error = f"HTTP {status}: {self._err_text(resp)}"
                    retry_after = self._retry_after(resp)
                else:
                    raise LLMBadRequest(f"HTTP {status}: {self._err_text(resp)}")
            if attempt <= self.config.max_retries:
                self._sleep(retry_after if retry_after is not None else self.config.backoff_s * 2 ** (attempt - 1))
        raise LLMUnavailable(f"{model}: {last_error} (após {self.config.max_retries + 1} tentativas)")

    def _parse_ok(self, resp, model, started, attempt) -> LLMResponse | None:
        try:
            data = resp.json()
        except ValueError:
            return None
        # o OpenRouter às vezes devolve HTTP 200 com {"error": ...} no corpo
        if "error" in data and not data.get("choices"):
            code = data["error"].get("code")
            if code in (401, 402, 403):
                raise LLMAuthError(str(data["error"]))
            if isinstance(code, int) and code < 500 and code != 429:
                raise LLMBadRequest(str(data["error"]))
            return None
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not text or not str(text).strip():
            return None
        usage = data.get("usage") or {}
        return LLMResponse(
            text=str(text),
            model=data.get("model", model),
            latency_s=round(time.monotonic() - started, 3),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            attempts=attempt,
        )

    @staticmethod
    def _err_text(resp) -> str:
        try:
            return str(resp.json().get("error", resp.text))[:300]
        except Exception:
            return (resp.text or "")[:300]

    @staticmethod
    def _retry_after(resp) -> float | None:
        try:
            return min(float(resp.headers.get("Retry-After")), 30.0)
        except (TypeError, ValueError):
            return None
