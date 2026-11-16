"""Orquestra o fluxo: fontes, normalização, classificação, deduplicação, resumo e priorização."""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ai import AIService
from .dedup import DedupConfig, cluster_reports
from .models import Occurrence
from .normalize import TZ, load_all
from .priority import Weights, score_occurrence
from .spatial import centroid, haversine_m, load_pois

log = logging.getLogger("citizentech.pipeline")


@dataclass
class PipelineResult:
    queue: list[Occurrence]
    triage: list[dict]
    discarded: list[dict]
    review_pairs: list[dict]
    merge_log: list[dict]
    stats: dict


def _address_of(reports, lat: float, lon: float) -> str:
    """Endereço do relato mais próximo do centro da ocorrência."""
    with_addr = [r for r in reports if r.address_text and r.lat is not None]
    if with_addr:
        return min(with_addr, key=lambda r: haversine_m(lat, lon, r.lat, r.lon)).address_text
    return f"coordenadas {lat:.5f}, {lon:.5f}"


def run_pipeline(data_dir: str | Path, ai: AIService, as_of: datetime | None = None, weights: Weights = Weights(), dedup_cfg: DedupConfig = DedupConfig()) -> PipelineResult:
    data_dir = Path(data_dir)
    as_of = as_of or datetime.now(TZ)
    reports = load_all(data_dir)
    pois = load_pois(data_dir / "points_of_interest.json")
    log.info("%d relatos normalizados de %d canais", len(reports), len({r.channel for r in reports}))

    analyses = [ai.analyze_report(r) for r in reports]
    log.info("classificação: %s", dict(Counter(a.source for a in analyses)))

    dd = cluster_reports(reports, analyses, ai.same_occurrence, dedup_cfg)
    log.info("deduplicação: %d relatos -> %d ocorrências (%d decisões do LLM, %d pares para revisão humana)",
             sum(len(c) for c in dd.clusters), len(dd.clusters),
             sum(1 for m in dd.merge_log if m["method"] != "regra_espacial"), len(dd.review_pairs))

    queue: list[Occurrence] = []
    for n, members in enumerate(dd.clusters, start=1):
        rs = [reports[i] for i in members]
        an = [analyses[i] for i in members]
        cat = Counter(a.category for a in an).most_common(1)[0][0]
        if {a.category for a in an} == {"alagamento", "bueiro_entupido"}:
            cat = "alagamento"  # causa e efeito fundidos: trata-se pelo efeito mais grave
        lat, lon = centroid([(r.lat, r.lon) for r in rs])
        occ = Occurrence(
            id=f"OC-{n:03d}", category=cat, reports=rs, analyses=an, lat=lat, lon=lon,
            address=_address_of(rs, lat, lon), first_seen=min(r.received_at for r in rs), last_seen=max(r.received_at for r in rs),
            risk_to_life=any(a.risk_to_life for a in an), blocks_passage=any(a.blocks_passage for a in an),
            vulnerable_people=any(a.vulnerable_people for a in an),
            ai_evidence=sorted({e.lower(): e for a in an for e in a.evidence}.values(), key=str.lower),
        )
        ordered = sorted(zip(rs, an), key=lambda t: (t[1].category != cat, t[0].received_at))
        summary, src = ai.summarize(cat, occ.address, [r.text for r, _ in ordered])
        occ.title, occ.summary, occ.suggested_team, occ.summary_source = summary.title, summary.summary, summary.suggested_team, src
        score_occurrence(occ, pois, as_of, weights)
        queue.append(occ)
    queue.sort(key=lambda o: (-o.score, -len(o.reports), o.first_seen))

    triage = [
        {"report_id": r.id, "channel": r.channel, "address": r.address_text, "category": a.category,
         "reason": "endereço não localizado; precisa de triagem humana"}
        for r, a in zip(reports, analyses) if r.lat is None and a.category != "nao_acionavel"
    ]
    discarded = [
        {"report_id": r.id, "channel": r.channel, "text": r.text[:120], "reason": "sem problema físico identificável (revisar se necessário)"}
        for r, a in zip(reports, analyses) if a.category == "nao_acionavel"
    ]
    stats = {
        "reports_in": len(reports), "occurrences": len(queue), "triage": len(triage), "discarded": len(discarded),
        "analysis_sources": dict(Counter(a.source for a in analyses)),
        "ai_calls": dict(ai.stats), "ai_by_task": ai.by_task, "llm_disabled_reason": ai.disabled_reason,
        "dedup_spatial_links": sum(1 for m in dd.merge_log if m["method"] == "regra_espacial"),
        "dedup_llm_merges": sum(1 for m in dd.merge_log if m["method"] == "llm"),
        "dedup_llm_rejections": sum(1 for m in dd.merge_log if m["method"] == "llm_rejeitou"),
        "dedup_review_pairs": len(dd.review_pairs),
    }
    return PipelineResult(queue, triage, discarded, dd.review_pairs, dd.merge_log, stats)


def to_json(res: PipelineResult, as_of: datetime, llm_info: dict) -> dict:
    return {
        "as_of": as_of.isoformat(), "llm": llm_info, "stats": res.stats,
        "queue": [
            {
                "rank": i, "occurrence_id": o.id, "category": o.category, "band": o.band, "score": o.score,
                "title": o.title, "summary": o.summary, "suggested_team": o.suggested_team,
                "n_reports": len(o.reports), "channels": sorted({r.channel for r in o.reports}),
                "location": {"lat": round(o.lat, 6), "lon": round(o.lon, 6), "address": o.address},
                "first_seen": o.first_seen.isoformat(), "report_ids": [r.id for r in o.reports],
                "factors": [f.as_dict() for f in o.factors],
                "ai_signals_need_human_confirmation": [f.name for f in o.factors if f.kind == "ia"],
                "ai_evidence": o.ai_evidence, "summary_source": o.summary_source,
                "warnings": sorted({w for a in o.analyses for w in a.warnings}),
            }
            for i, o in enumerate(res.queue, start=1)
        ],
        "triage": res.triage, "discarded": res.discarded,
        "dedup": {
            "llm_decisions": [m for m in res.merge_log if m["method"] != "regra_espacial"],
            "pairs_for_human_review": res.review_pairs,
        },
    }


def write_outputs(res: PipelineResult, as_of: datetime, llm_info: dict, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = to_json(res, as_of, llm_info)
    jp = out / "priority_queue.json"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mp = out / "priority_queue.md"
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return jp, mp


def render_markdown(p: dict) -> str:
    lines = [
        "# Fila de priorização", "",
        f"_Referência: {p['as_of']} · LLM: {p['llm'].get('backend')} / {p['llm'].get('model')}_", "",
        f"**{p['stats']['reports_in']} relatos → {p['stats']['occurrences']} ocorrências** "
        f"({p['stats']['triage']} para triagem de endereço, {p['stats']['discarded']} sem problema identificável). "
        "Pontos marcados com 🤖 vêm de sinais sugeridos por IA e precisam de confirmação humana.", "",
        "| # | Pts | Faixa | Tipo | Relatos | Local | Por quê (maiores fatores) |",
        "|---|-----|-------|------|---------|-------|---------------------------|",
    ]
    for o in p["queue"]:
        top = sorted(o["factors"], key=lambda f: -f["points"])[:4]
        why = "; ".join(f"{'🤖 ' if f['kind'] == 'ia' else ''}{f['detail']} (+{f['points']:g})" for f in top)
        lines.append(f"| {o['rank']} | {o['score']:g} | {o['band']} | {o['category']} | {o['n_reports']} | {o['location']['address']} | {why} |")
    return "\n".join(lines) + "\n"
