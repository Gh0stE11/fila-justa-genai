from datetime import datetime

from citizentech_ai.models import Occurrence
from citizentech_ai.priority import Weights, band_for, score_occurrence
from citizentech_ai.spatial import POI
from tests.conftest import TZ, make_report

AS_OF = datetime(2026, 11, 9, tzinfo=TZ)
SCHOOL = POI("S", "escola", "Escola Teste", -23.56, -46.64)
HOSP = POI("H", "hospital", "Hospital Teste", -23.56, -46.64)


def occ(cat="buraco", n=1, lat=-23.5, lon=-46.5, risk=False, block=False, vuln=False, first=datetime(2026, 11, 9, tzinfo=TZ)):
    rs = [make_report(f"R{i}", lat, lon, when=first) for i in range(n)]
    return Occurrence("OC", cat, rs, [], lat, lon, "x", first, first, risk, block, vuln)


def names(o):
    return {f.name: f for f in o.factors}


def test_score_is_sum_of_explained_factors():
    o = occ("alagamento", n=4, first=datetime(2026, 11, 7, tzinfo=TZ), lat=-23.56, lon=-46.64)
    score_occurrence(o, [SCHOOL], AS_OF)
    assert o.score == round(sum(f.points for f in o.factors), 1) == 30 + 12 + 4 + 12
    assert all(f.detail for f in o.factors)


def test_proximity_requires_radius():
    near, far = occ(lat=-23.5601, lon=-46.64), occ(lat=-23.57, lon=-46.64)
    for o in (near, far):
        score_occurrence(o, [HOSP], AS_OF)
    assert "proximidade_hospital" in names(near) and "proximidade_hospital" not in names(far)


def test_volume_grows_with_log_and_is_capped():
    pts = []
    for n in (1, 2, 8, 1000):
        o = occ(n=n)
        score_occurrence(o, [], AS_OF)
        pts.append(names(o).get("volume_de_relatos").points if n > 1 else 0)
    assert pts == [0, 6.0, 15.0, 15.0]


def test_age_adds_points_up_to_cap():
    o = occ(first=datetime(2026, 10, 1, tzinfo=TZ))
    score_occurrence(o, [], AS_OF)
    assert names(o)["tempo_em_aberto"].points == 10


def test_ai_signals_are_capped_and_marked():
    o = occ("lixo", risk=True, block=True, vuln=True)
    score_occurrence(o, [], AS_OF)
    ai = [f for f in o.factors if f.kind == "ia"]
    assert len(ai) == 3 and sum(f.points for f in ai) <= 25.0 and all("confirmar" in f.detail for f in ai)


def test_ai_cannot_lift_minor_issue_above_severe_rule_based_one():
    minor_with_ai = occ("lixo", risk=True, block=True, vuln=True)
    severe = occ("alagamento", n=8, lat=-23.56, lon=-46.64)
    score_occurrence(minor_with_ai, [], AS_OF)
    score_occurrence(severe, [SCHOOL], AS_OF)
    assert minor_with_ai.score < severe.score


def test_bands():
    w = Weights()
    assert [band_for(s, w) for s in (95, 70, 69.9, 50, 30, 29.9, 0)] == ["critica", "critica", "alta", "alta", "media", "baixa", "baixa"]


def test_score_never_exceeds_100():
    o = occ("alagamento", n=50, risk=True, block=True, vuln=True, first=datetime(2026, 10, 1, tzinfo=TZ), lat=-23.56, lon=-46.64)
    score_occurrence(o, [SCHOOL, HOSP, POI("R", "area_de_risco", "R", -23.56, -46.64)], AS_OF)
    assert o.score <= 100
