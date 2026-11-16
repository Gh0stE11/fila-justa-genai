from citizentech_ai.ai import AIService  # noqa: F401
from citizentech_ai.dedup import cluster_reports
from citizentech_ai.models import Analysis
from citizentech_ai.schemas import DuplicateVerdict
from tests.conftest import make_report


def an(cat):
    return Analysis(cat, False, False, False, [], 0.9, "", "llm")


def never(*a):
    raise AssertionError("LLM não deveria ser chamado")


# 0.00045 grau de latitude equivale a uns 50 m
def test_same_category_within_80m_merges_without_llm():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.56045, -46.64)]
    assert cluster_reports(rs, [an("buraco"), an("buraco")], never).clusters == [[0, 1]]


def test_same_category_far_apart_stays_separate():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.5700, -46.64)]
    assert cluster_reports(rs, [an("buraco")] * 2, never).clusters == [[0], [1]]


def test_different_unrelated_categories_never_merge():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.56001, -46.64)]
    assert len(cluster_reports(rs, [an("buraco"), an("lixo")], never).clusters) == 2


def test_gray_zone_llm_yes_merges():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.56045, -46.64)]
    calls = []

    def judge(a, ca, b, cb, d):
        calls.append((ca, cb, round(d)))
        return DuplicateVerdict(same_occurrence=True, confidence=0.9, reason="causa e efeito")

    res = cluster_reports(rs, [an("alagamento"), an("bueiro_entupido")], judge)
    assert res.clusters == [[0, 1]] and len(calls) == 1 and res.merge_log[0]["method"] == "llm"


def test_gray_zone_llm_no_or_low_confidence_keeps_separate():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.56045, -46.64)]
    for v in (DuplicateVerdict(same_occurrence=False, confidence=0.9, reason="x"),
              DuplicateVerdict(same_occurrence=True, confidence=0.4, reason="x")):
        res = cluster_reports(rs, [an("alagamento"), an("bueiro_entupido")], lambda *a, v=v: v)
        assert len(res.clusters) == 2 and res.merge_log[0]["method"] == "llm_rejeitou"


def test_llm_unavailable_does_not_merge_and_flags_for_review():
    rs = [make_report("A", -23.5600, -46.64), make_report("B", -23.56045, -46.64)]
    res = cluster_reports(rs, [an("alagamento"), an("bueiro_entupido")], lambda *a: None)
    assert len(res.clusters) == 2 and res.review_pairs and res.review_pairs[0]["a"] == "A"


def test_reports_without_location_or_actionable_are_excluded():
    rs = [make_report("A", None, None), make_report("B", -23.5, -46.6), make_report("C", -23.5, -46.6)]
    res = cluster_reports(rs, [an("buraco"), an("nao_acionavel"), an("buraco")], never)
    assert res.clusters == [[2]]


def test_old_reports_beyond_time_window_not_merged():
    from datetime import datetime, timedelta
    from tests.conftest import TZ
    t0 = datetime(2026, 10, 1, tzinfo=TZ)
    rs = [make_report("A", -23.56, -46.64, when=t0), make_report("B", -23.56, -46.64, when=t0 + timedelta(days=30))]
    assert len(cluster_reports(rs, [an("buraco")] * 2, never).clusters) == 2
