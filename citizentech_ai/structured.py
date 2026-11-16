"""Saída estruturada: extrai o JSON, valida, tenta reparar e rebaixa o modo se preciso."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .audit import AuditLog
from .llm_client import LLMBackend, LLMBadRequest
from .schemas import to_strict_schema

T = TypeVar("T", bound=BaseModel)

# se o provedor recusar o response_format, tenta o modo seguinte
DOWNGRADE = {"json_schema": "json_object", "json_object": "prompt_only"}

REPAIR_PROMPT = (
    "Sua resposta anterior não passou na validação: {error}\n"
    "Responda de novo SOMENTE com um objeto JSON válido que siga exatamente o formato pedido, "
    "sem texto antes ou depois e sem markdown."
)


class StructuredOutputError(Exception):
    """A resposta não é utilizável nem após o reparo."""


@dataclass
class StructuredResult(Generic[T]):
    value: T
    model: str
    repaired: bool
    attempts: int
    latency_s: float
    response_mode: str


def extract_json(text: str) -> Any:
    """Extrai JSON puro, em bloco ```json``` ou cercado de texto."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("nenhum objeto JSON encontrado na resposta")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError("objeto JSON incompleto/truncado")


def call_structured(
    backend: LLMBackend,
    messages: list[dict[str, str]],
    model_cls: type[T],
    *,
    task: str,
    response_mode: str = "json_schema",
    max_repairs: int = 1,
    audit: AuditLog | None = None,
) -> StructuredResult[T]:
    """Erros de rede e autenticação sobem para o chamador decidir o fallback."""
    schema = to_strict_schema(model_cls)
    mode = response_mode
    msgs = list(messages)
    repaired = False
    total_attempts = 0
    total_latency = 0.0
    last_error = "?"
    for round_no in range(max_repairs + 1):
        while True:
            try:
                resp = backend.complete(
                    msgs,
                    schema=schema if mode == "json_schema" else None,
                    schema_name=model_cls.__name__,
                    response_mode=mode,
                    task=task,
                )
                break
            except LLMBadRequest as exc:
                nxt = DOWNGRADE.get(mode)
                if audit:
                    audit.record(task=task, event="mode_downgrade", from_mode=mode, to_mode=nxt, error=str(exc)[:200])
                if nxt is None:
                    raise StructuredOutputError(f"provedor recusou a requisição: {exc}") from exc
                mode = nxt
        total_attempts += resp.attempts
        total_latency += resp.latency_s
        try:
            value = model_cls.model_validate(extract_json(resp.text))
        except ValueError as exc:  # JSONDecodeError e ValidationError herdam de ValueError
            last_error = str(exc).replace("\n", " ")[:300]
            if audit:
                audit.record(task=task, event="invalid_output", round=round_no, error=last_error, model=resp.model)
            if round_no < max_repairs:
                repaired = True
                msgs = [
                    *messages,
                    {"role": "assistant", "content": resp.text[:1500]},
                    {"role": "user", "content": REPAIR_PROMPT.format(error=last_error)},
                ]
            continue
        if audit:
            audit.record(
                task=task, event="ok", model=resp.model, repaired=repaired, mode=mode,
                latency_s=resp.latency_s, prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
            )
        return StructuredResult(value, resp.model, repaired, total_attempts, round(total_latency, 3), mode)
    raise StructuredOutputError(f"saída inválida após {max_repairs + 1} tentativa(s): {last_error}")
