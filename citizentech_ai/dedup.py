"""Deduplicação em dois estágios: regra espacial e, na zona cinzenta, desempate pelo LLM."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .models import Analysis, Report
from .schemas import DuplicateVerdict
from .spatial import haversine_m

log = logging.getLogger("citizentech.dedup")
RELATED = {frozenset({"alagamento", "bueiro_entupido"})}


@dataclass
class DedupConfig:
    merge_radius_m: float = 80.0   # mesma categoria dentro deste raio = mesma ocorrência (sem IA)
    gray_radius_m: float = 150.0   # zona cinzenta: o LLM desempata
    max_gap_days: float = 14.0
    min_llm_confidence: float = 0.7


@dataclass
class DedupResult:
    clusters: list[list[int]]
    merge_log: list[dict] = field(default_factory=list)
    review_pairs: list[dict] = field(default_factory=list)


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _compatible(c1: str, c2: str) -> bool:
    return c1 == c2 or frozenset({c1, c2}) in RELATED


def cluster_reports(
    reports: list[Report],
    analyses: list[Analysis],
    judge: Callable[[Report, str, Report, str, float], DuplicateVerdict | None],
    cfg: DedupConfig = DedupConfig(),
) -> DedupResult:
    idx = [i for i, (r, a) in enumerate(zip(reports, analyses)) if r.lat is not None and a.category != "nao_acionavel"]
    uf = _UF(len(reports))
    result = DedupResult(clusters=[])

    def dist(i: int, j: int) -> float:
        return haversine_m(reports[i].lat, reports[i].lon, reports[j].lat, reports[j].lon)

    def gap_days(i: int, j: int) -> float:
        return abs((reports[i].received_at - reports[j].received_at).total_seconds()) / 86400

    # estágio 1: mesma categoria, perto e em datas próximas
    for a_pos, i in enumerate(idx):
        for j in idx[a_pos + 1:]:
            if analyses[i].category == analyses[j].category and gap_days(i, j) <= cfg.max_gap_days:
                d = dist(i, j)
                if d <= cfg.merge_radius_m:
                    uf.union(i, j)
                    result.merge_log.append({"a": reports[i].id, "b": reports[j].id, "method": "regra_espacial", "distance_m": round(d)})

    # estágio 2: um pedido ao LLM por par de grupos, com o par de relatos mais próximo
    candidates = []
    groups: dict[int, list[int]] = {}
    for i in idx:
        groups.setdefault(uf.find(i), []).append(i)
    roots = sorted(groups)
    for x, ra in enumerate(roots):
        for rb in roots[x + 1:]:
            best = min(((dist(i, j), i, j) for i in groups[ra] for j in groups[rb]), key=lambda t: t[0])
            cat_a = Counter(analyses[i].category for i in groups[ra]).most_common(1)[0][0]
            cat_b = Counter(analyses[i].category for i in groups[rb]).most_common(1)[0][0]
            if best[0] <= cfg.gray_radius_m and _compatible(cat_a, cat_b) and gap_days(best[1], best[2]) <= cfg.max_gap_days:
                candidates.append((best[0], best[1], best[2], cat_a, cat_b))
    for d, i, j, cat_a, cat_b in sorted(candidates):
        if uf.find(i) == uf.find(j):
            continue
        verdict = judge(reports[i], cat_a, reports[j], cat_b, d)
        pair = {"a": reports[i].id, "b": reports[j].id, "distance_m": round(d)}
        if verdict is None:
            result.review_pairs.append({**pair, "reason": "LLM indisponível; não foi fundido, revisar manualmente"})
        elif verdict.same_occurrence and verdict.confidence >= cfg.min_llm_confidence:
            uf.union(i, j)
            result.merge_log.append({**pair, "method": "llm", "confidence": verdict.confidence, "reason": verdict.reason})
        else:
            result.merge_log.append({**pair, "method": "llm_rejeitou", "confidence": verdict.confidence, "reason": verdict.reason})

    final: dict[int, list[int]] = {}
    for i in range(len(reports)):
        if reports[i].lat is None or analyses[i].category == "nao_acionavel":
            continue
        final.setdefault(uf.find(i), []).append(i)
    result.clusters = sorted(final.values(), key=lambda c: c[0])
    return result
