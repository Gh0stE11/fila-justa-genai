"""Priorização por regras, com origem e justificativa registradas para cada ponto.

Os sinais da IA (risco à vida, bloqueio, vulneráveis) valem pouco e têm teto. Aparecem marcados
como 'ia' e precisam de confirmação humana.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .models import Factor, Occurrence
from .spatial import POI, nearby


@dataclass(frozen=True)
class Weights:
    category_base: dict = field(default_factory=lambda: {
        "alagamento": 30, "arvore_caida": 25, "bueiro_entupido": 18, "buraco": 15,
        "iluminacao": 10, "lixo": 6, "outros": 3,
    })
    volume_per_doubling: float = 6.0   # pontos por dobra de relatos (log2)
    volume_cap: float = 15.0
    age_per_day: float = 2.0
    age_cap: float = 10.0
    near_hospital: float = 15.0
    near_school: float = 12.0
    near_risk_area: float = 10.0
    poi_radius_m: dict = field(default_factory=lambda: {"hospital": 300.0, "escola": 300.0, "area_de_risco": 200.0})
    ai_risk_to_life: float = 15.0
    ai_blocks_passage: float = 8.0
    ai_vulnerable: float = 7.0
    ai_cap: float = 25.0
    bands: tuple = (("critica", 70.0), ("alta", 50.0), ("media", 30.0), ("baixa", 0.0))


def band_for(score: float, w: Weights) -> str:
    for name, floor in w.bands:
        if score >= floor:
            return name
    return "baixa"


def score_occurrence(occ: Occurrence, pois: list[POI], as_of: datetime, w: Weights = Weights()) -> None:
    f: list[Factor] = []
    base = w.category_base.get(occ.category, 3)
    f.append(Factor("tipo_do_problema", base, "regra", f"categoria '{occ.category}'"))

    n = len(occ.reports)
    if n > 1:
        pts = min(w.volume_cap, round(w.volume_per_doubling * math.log2(n), 1))
        f.append(Factor("volume_de_relatos", pts, "regra", f"{n} relatos independentes no mesmo ponto"))

    age_days = max(0.0, (as_of - occ.first_seen).total_seconds() / 86400)
    pts = min(w.age_cap, round(w.age_per_day * age_days, 1))
    if pts > 0:
        f.append(Factor("tempo_em_aberto", pts, "regra", f"aberto há {age_days:.1f} dia(s)"))

    if occ.lat is not None:
        for kind, label, pts_w in (("hospital", "hospital", w.near_hospital), ("escola", "escola", w.near_school), ("area_de_risco", "área de risco", w.near_risk_area)):
            hits = nearby(occ.lat, occ.lon, pois, kind, w.poi_radius_m[kind])
            if hits:
                poi, d = hits[0]
                f.append(Factor(f"proximidade_{kind}", pts_w, "espacial", f"{label} '{poi.name}' a {d:.0f} m"))

    ai: list[Factor] = []
    if occ.risk_to_life:
        ai.append(Factor("ia_risco_a_vida", w.ai_risk_to_life, "ia", "relato descreve perigo à integridade física (confirmar)"))
    if occ.blocks_passage:
        ai.append(Factor("ia_bloqueio_de_passagem", w.ai_blocks_passage, "ia", "relato indica passagem bloqueada (confirmar)"))
    if occ.vulnerable_people:
        ai.append(Factor("ia_pessoas_vulneraveis", w.ai_vulnerable, "ia", "relato cita crianças/idosos/escola (confirmar)"))
    total_ai = sum(x.points for x in ai)
    if total_ai > w.ai_cap and ai:
        scale = w.ai_cap / total_ai
        ai = [Factor(x.name, round(x.points * scale, 1), x.kind, x.detail + f" [teto de {w.ai_cap:g} pts para sinais de IA]") for x in ai]
    f.extend(ai)

    occ.factors = f
    occ.score = round(min(100.0, sum(x.points for x in f)), 1)
    occ.band = band_for(occ.score, w)
