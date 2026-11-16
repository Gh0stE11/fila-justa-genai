"""Backend offline com a mesma interface do OpenRouterClient, para testes e demonstrações.

Usa regras simples no lugar de um modelo. Com `chaos=True` injeta falhas (JSON em markdown, enum
inválido, texto livre, 503) para exercitar o tratamento de erros. Não mede a qualidade de um LLM real.
"""
from __future__ import annotations

import json
import re

from .heuristics import classify_by_rules
from .llm_client import LLMResponse, LLMUnavailable
from .privacy import fold


def _tag(messages: list[dict[str, str]], tag: str) -> str | None:
    for m in reversed(messages):
        if m["role"] != "user":
            continue
        found = re.search(rf"<{tag}[^>]*>\n?(.*?)\n?</{tag}>", m["content"], re.DOTALL)
        if found:
            return found.group(1)
    return None


class MockBackend:
    name = "mock"

    def __init__(self, chaos: bool = False):
        self.chaos = chaos
        self.calls = 0

    def complete(self, messages, *, schema=None, schema_name="response", response_mode="json_schema", task="generic") -> LLMResponse:
        self.calls += 1
        n = self.calls
        if self.chaos and n % 14 == 0:
            raise LLMUnavailable("simulado: HTTP 503 após retries")
        payload = self._answer(messages, task)
        text = json.dumps(payload, ensure_ascii=False)
        if self.chaos:
            if n % 23 in (0, 1):
                text = "Desculpe, não consigo ajudar com esse pedido."
            elif n % 9 == 0 and "category" in payload:
                text = json.dumps({**payload, "category": "inundacao"}, ensure_ascii=False)
            elif n % 6 == 0:
                text = f"Claro! Aqui está a análise:\n```json\n{text}\n```\nEspero ter ajudado."
        return LLMResponse(text=text, model="mock/simulado", latency_s=0.0, prompt_tokens=0, completion_tokens=0)

    def _answer(self, messages, task: str) -> dict:
        if task == "analyze_report":
            text = _tag(messages, "relato") or ""
            hint = re.search(r"Categoria escolhida pelo cidadão no app: (\w+)", messages[-1]["content"])
            return classify_by_rules(text, hint.group(1) if hint else None).model_dump()
        if task == "same_occurrence":
            a = fold(_tag(messages, "relato_a") or "")
            b = fold(_tag(messages, "relato_b") or "")
            dist = float(re.search(r"Distância entre os pontos: (\d+)", messages[-1]["content"]).group(1))
            water = ("alag", "agua", "bueiro", "enchente", "entupid")
            same = dist <= 150 and any(w in a for w in water) and any(w in b for w in water)
            return {
                "same_occurrence": same,
                "confidence": 0.82 if same else 0.6,
                "reason": "Ambos tratam de água/escoamento no mesmo trecho." if same else "Sem relação clara entre os relatos.",
            }
        if task == "summarize_cluster":
            body = messages[-1]["content"]
            head = re.search(r"Categoria: (\w+) \| Endereço: (.*?) \| Total de relatos: (\d+)", body)
            cat, addr, n = (head.group(1), head.group(2), head.group(3)) if head else ("outros", "?", "1")
            first = re.search(r"- (.*)", body)
            team = {
                "alagamento": "drenagem", "bueiro_entupido": "drenagem", "buraco": "tapa_buraco",
                "arvore_caida": "poda_arvores", "iluminacao": "iluminacao", "lixo": "limpeza_urbana",
            }.get(cat, "triagem_humana")
            return {
                "title": f"{cat.replace('_', ' ').capitalize()} em {addr}"[:80],
                "summary": f"{n} relato(s) sobre o mesmo ponto. Ex.: {first.group(1) if first else ''}"[:300],
                "suggested_team": team,
            }
        raise ValueError(f"tarefa desconhecida no mock: {task}")
