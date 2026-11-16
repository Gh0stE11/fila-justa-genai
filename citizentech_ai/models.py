from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Report:
    id: str
    channel: str  # app | whatsapp | central_156 | planilha
    received_at: datetime
    text: str  # JÁ sem dados pessoais
    address_text: str | None
    lat: float | None
    lon: float | None
    loc_source: str  # gps | gazetteer | gazetteer_street | unresolved
    hint_category: str | None = None


@dataclass
class Analysis:
    category: str
    risk_to_life: bool
    blocks_passage: bool
    vulnerable_people: bool
    evidence: list[str]
    confidence: float
    summary: str
    source: str  # llm | llm_repaired | fallback_rules
    model: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Factor:
    name: str
    points: float
    kind: str  # regra | espacial | ia
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "points": self.points, "kind": self.kind, "detail": self.detail}


@dataclass
class Occurrence:
    id: str
    category: str
    reports: list[Report]
    analyses: list[Analysis]
    lat: float | None
    lon: float | None
    address: str
    first_seen: datetime
    last_seen: datetime
    risk_to_life: bool = False
    blocks_passage: bool = False
    vulnerable_people: bool = False
    ai_evidence: list[str] = field(default_factory=list)
    title: str = ""
    summary: str = ""
    suggested_team: str = "triagem_humana"
    summary_source: str = "fallback_rules"
    score: float = 0.0
    band: str = "baixa"
    factors: list[Factor] = field(default_factory=list)
