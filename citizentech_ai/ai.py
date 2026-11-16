"""Tarefas de IA do pipeline (classificar, desempatar duplicata, resumir), cada uma com fallback por regras."""
from __future__ import annotations

import logging

from . import prompts
from .audit import AuditLog
from .heuristics import classify_by_rules
from .llm_client import LLMAuthError, LLMBackend, LLMError
from .models import Analysis, Report
from .privacy import fold
from .schemas import ClusterSummary, DuplicateVerdict, ReportAnalysis
from .structured import StructuredOutputError, call_structured

log = logging.getLogger("citizentech.ai")
MIN_CONFIDENCE_FOR_FLAGS = 0.5
TEAM_BY_CATEGORY = {
    "alagamento": "drenagem", "bueiro_entupido": "drenagem", "buraco": "tapa_buraco",
    "arvore_caida": "poda_arvores", "iluminacao": "iluminacao", "lixo": "limpeza_urbana",
}


class AIService:
    def __init__(self, backend: LLMBackend | None, response_mode: str = "json_schema", audit: AuditLog | None = None):
        self.backend = backend
        self.response_mode = response_mode
        self.audit = audit or AuditLog(None)
        self.stats = {"llm_ok": 0, "llm_repaired": 0, "fallback": 0}  # só classificação de relatos
        self.by_task: dict[str, dict[str, int]] = {}  # todas as tarefas de IA
        self.disabled_reason: str | None = None  # setado se a chave for inválida: para de tentar

    def _count(self, task: str, key: str) -> None:
        self.by_task.setdefault(task, {"llm_ok": 0, "llm_repaired": 0, "fallback": 0})[key] += 1

    def _structured(self, messages, model_cls, task):
        if self.backend is None or self.disabled_reason:
            self._count(task, "fallback")
            return None
        try:
            res = call_structured(
                self.backend, messages, model_cls, task=task,
                response_mode=self.response_mode, audit=self.audit,
            )
            self._count(task, "llm_repaired" if res.repaired else "llm_ok")
            return res
        except LLMAuthError as exc:
            self.disabled_reason = f"autenticação: {exc}"
            log.error("LLM desativado para o resto da execução (%s)", self.disabled_reason)
            self.audit.record(task=task, event="llm_disabled", reason=self.disabled_reason[:200])
        except (LLMError, StructuredOutputError) as exc:
            log.warning("[%s] falha do LLM, usando regras: %s", task, exc)
            self.audit.record(task=task, event="fallback", error=str(exc)[:300])
        self._count(task, "fallback")
        return None

    def analyze_report(self, r: Report) -> Analysis:
        messages = prompts.analyze_messages(r.text, r.channel, r.address_text, r.hint_category)
        res = self._structured(messages, ReportAnalysis, "analyze_report")
        if res is None:
            self.stats["fallback"] += 1
            base = classify_by_rules(r.text, r.hint_category)
            return Analysis(**self._flags(base, r.text, check=False), source="fallback_rules")
        key = "llm_repaired" if res.repaired else "llm_ok"
        self.stats[key] += 1
        data = self._flags(res.value, r.text, check=True)
        return Analysis(**data, source="llm_repaired" if res.repaired else "llm", model=res.model)

    @staticmethod
    def _flags(a: ReportAnalysis, text: str, check: bool) -> dict:
        """Um sinal do LLM só vale se a evidência aparecer literalmente no texto e a confiança for suficiente."""
        warnings: list[str] = []
        flags = {"risk_to_life": a.risk_to_life, "blocks_passage": a.blocks_passage, "vulnerable_people": a.vulnerable_people}
        evidence = list(a.evidence)
        if check and any(flags.values()):
            ft = fold(text)
            grounded = [e for e in evidence if len(e.strip()) >= 3 and fold(e).strip() in ft]
            if not grounded:
                warnings.append("sinais descartados: evidência não encontrada no texto (possível alucinação)")
                flags = {k: False for k in flags}
            elif len(grounded) < len(evidence):
                warnings.append("parte da evidência não consta no texto e foi ignorada")
            evidence = grounded
            if a.confidence < MIN_CONFIDENCE_FOR_FLAGS and any(flags.values()):
                warnings.append(f"sinais descartados: confiança {a.confidence:.2f} < {MIN_CONFIDENCE_FOR_FLAGS}")
                flags = {k: False for k in flags}
        return {
            "category": a.category, **flags, "evidence": evidence,
            "confidence": a.confidence, "summary": a.summary, "warnings": warnings,
        }

    def same_occurrence(self, a: Report, a_cat: str, b: Report, b_cat: str, dist_m: float) -> DuplicateVerdict | None:
        """Devolve None quando o LLM não responde; nesse caso o par não é fundido e vai para revisão."""
        res = self._structured(prompts.duplicate_messages(a.text, a_cat, b.text, b_cat, dist_m), DuplicateVerdict, "same_occurrence")
        return res.value if res else None

    def summarize(self, category: str, address: str, texts: list[str]) -> tuple[ClusterSummary, str]:
        res = self._structured(prompts.summary_messages(category, address, len(texts), texts), ClusterSummary, "summarize_cluster")
        if res is not None:
            return res.value, "llm"
        label = category.replace("_", " ").capitalize()
        return ClusterSummary(
            title=f"{label} em {address}"[:80],
            summary=f"{len(texts)} relato(s) sobre o mesmo ponto. Ex.: {texts[0] if texts else ''}"[:300],
            suggested_team=TEAM_BY_CATEGORY.get(category, "triagem_humana"),
        ), "fallback_rules"
