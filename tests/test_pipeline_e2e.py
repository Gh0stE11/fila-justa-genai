from datetime import datetime
from pathlib import Path

import json

from citizentech_ai.ai import AIService
from citizentech_ai.mock_backend import MockBackend
from citizentech_ai.pipeline import run_pipeline, to_json, write_outputs
from tests.conftest import TZ

DATA = Path(__file__).resolve().parent.parent / "data"
AS_OF = datetime(2026, 11, 9, tzinfo=TZ)


def run(backend):
    return run_pipeline(DATA, AIService(backend), AS_OF)


def test_flood_near_school_is_first_and_explained():
    res = run(MockBackend())
    top = res.queue[0]
    assert top.category == "alagamento" and len(top.reports) >= 9 and top.band == "critica"
    assert {r.channel for r in top.reports} == {"app", "whatsapp", "central_156"}
    kinds = {f.name for f in top.factors}
    assert {"tipo_do_problema", "volume_de_relatos", "proximidade_escola"} <= kinds
    assert res.queue == sorted(res.queue, key=lambda o: (-o.score, -len(o.reports), o.first_seen))


def test_llm_tiebreak_merges_drain_and_flood():
    res = run(MockBackend())
    assert res.stats["dedup_llm_merges"] == 1
    assert not any(o.category == "bueiro_entupido" for o in res.queue)


def test_prompt_injection_report_does_not_gain_priority():
    res = run(MockBackend())
    inj = next(o for o in res.queue if any("Ignore as instruções" in r.text for r in o.reports))
    assert inj.band == "baixa" and not any(f.kind == "ia" for f in inj.factors)


def test_edge_cases_go_to_triage_and_discarded():
    res = run(MockBackend())
    assert [t["report_id"] for t in res.triage] == ["WPP-011"]
    assert "WPP-009" in {d["report_id"] for d in res.discarded}


def test_no_personal_data_in_outputs(tmp_path):
    res = run(MockBackend())
    jp, mp = write_outputs(res, AS_OF, {"backend": "mock"}, tmp_path)
    blob = jp.read_text(encoding="utf-8") + mp.read_text(encoding="utf-8")
    for leaked in ("98765", "maria.silva", "+55", "91234"):
        assert leaked not in blob
    assert json.loads(jp.read_text(encoding="utf-8"))["queue"][0]["rank"] == 1


def test_works_without_any_llm_and_flags_pair_for_review():
    res = run(None)
    assert res.queue[0].category == "alagamento"
    assert res.stats["analysis_sources"] == {"fallback_rules": 26}
    assert res.stats["dedup_review_pairs"] == 1


def test_chaos_mode_completes_and_keeps_same_top_item():
    res = run(MockBackend(chaos=True))
    src = res.stats["analysis_sources"]
    assert src.get("llm_repaired", 0) > 0 and src.get("fallback_rules", 0) > 0
    assert res.queue[0].category == "alagamento"
    assert len(to_json(res, AS_OF, {})["queue"]) == len(res.queue)


def test_deterministic_output():
    a = to_json(run(MockBackend()), AS_OF, {})
    b = to_json(run(MockBackend()), AS_OF, {})
    assert a == b
