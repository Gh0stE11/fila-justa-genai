"""Formato esperado das respostas do LLM (Pydantic) e conversão para JSON Schema estrito."""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "alagamento",
    "buraco",
    "bueiro_entupido",
    "arvore_caida",
    "iluminacao",
    "lixo",
    "outros",
    "nao_acionavel",
]
CATEGORIES: tuple[str, ...] = get_args(Category)

Team = Literal[
    "drenagem", "tapa_buraco", "poda_arvores", "iluminacao", "limpeza_urbana", "triagem_humana"
]


class ReportAnalysis(BaseModel):
    """Análise de um relato. O LLM extrai sinais, a prioridade é calculada por regras."""

    model_config = ConfigDict(extra="ignore")

    category: Category = Field(description="Tipo do problema; exatamente um valor da lista.")
    risk_to_life: bool = Field(
        description="true só se o relato descreve explicitamente perigo à vida ou integridade física."
    )
    blocks_passage: bool = Field(
        description="true só se o relato diz que o problema impede ou bloqueia passagem de pessoas ou veículos."
    )
    vulnerable_people: bool = Field(
        description="true só se o relato cita crianças, idosos, cadeirantes, gestantes ou escola/creche afetadas."
    )
    evidence: list[str] = Field(
        description="Até 3 trechos LITERAIS do relato que justificam as flags verdadeiras; [] se nenhuma."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confiança da classificação, de 0 a 1.")
    summary: str = Field(description="Resumo neutro do relato em até 160 caracteres.")

    @field_validator("evidence")
    @classmethod
    def _cap_evidence(cls, v: list[str]) -> list[str]:
        return [str(x) for x in v][:3]

    @field_validator("summary")
    @classmethod
    def _cap_summary(cls, v: str) -> str:
        return v.strip()[:160]


class DuplicateVerdict(BaseModel):
    """Dois relatos descrevem a mesma ocorrência?"""

    model_config = ConfigDict(extra="ignore")

    same_occurrence: bool = Field(description="true se são o mesmo problema no mesmo ponto.")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="Justificativa curta (até 200 caracteres).")

    @field_validator("reason")
    @classmethod
    def _cap_reason(cls, v: str) -> str:
        return v.strip()[:200]


class ClusterSummary(BaseModel):
    """Texto para a equipe da prefeitura."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Título curto da ocorrência (até 80 caracteres).")
    summary: str = Field(description="Resumo em 1-2 frases do que os relatos descrevem.")
    suggested_team: Team = Field(description="Equipe sugerida; triagem_humana se não estiver claro.")

    @field_validator("title")
    @classmethod
    def _cap_title(cls, v: str) -> str:
        return v.strip()[:80]

    @field_validator("summary")
    @classmethod
    def _cap_summary(cls, v: str) -> str:
        return v.strip()[:300]


_UNSUPPORTED_IN_STRICT = {
    "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "default", "title",
}


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema para o modo `strict`: additionalProperties=false, tudo required e sem keywords
    não suportadas. "title" é uma keyword a remover, mas também nome de campo; por isso os nomes
    dentro de `properties` não são filtrados."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in _UNSUPPORTED_IN_STRICT:
                    continue
                if k == "properties" and isinstance(v, dict):
                    out[k] = {name: clean(sub) for name, sub in v.items()}  # preserva nomes de campos
                else:
                    out[k] = clean(v)
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [clean(x) for x in node]
        return node

    return clean(model.model_json_schema())
