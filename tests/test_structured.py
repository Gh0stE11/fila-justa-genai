import pytest

from citizentech_ai.audit import AuditLog
from citizentech_ai.llm_client import LLMBadRequest, LLMUnavailable
from citizentech_ai.schemas import ClusterSummary, DuplicateVerdict, ReportAnalysis, to_strict_schema
from citizentech_ai.structured import StructuredOutputError, call_structured, extract_json
from tests.conftest import ScriptedBackend


def test_valid_json(good_analysis_json):
    be = ScriptedBackend([good_analysis_json])
    res = call_structured(be, [{"role": "user", "content": "x"}], ReportAnalysis, task="t")
    assert res.value.category == "alagamento" and not res.repaired
    assert be.calls[0]["schema"]["additionalProperties"] is False and be.calls[0]["mode"] == "json_schema"


def test_json_inside_markdown_fence_with_chatter(good_analysis_json):
    be = ScriptedBackend([f"Claro! Segue:\n```json\n{good_analysis_json}\n```\nEspero ter ajudado."])
    assert call_structured(be, [], ReportAnalysis, task="t").value.blocks_passage is True


def test_json_surrounded_by_text_without_fence(good_analysis_json):
    be = ScriptedBackend([f"Resposta: {good_analysis_json} fim"])
    assert call_structured(be, [], ReportAnalysis, task="t").value.confidence == 0.9


def test_invalid_enum_triggers_repair(good_analysis_json):
    bad = good_analysis_json.replace("alagamento", "inundacao")
    be = ScriptedBackend([bad, good_analysis_json])
    res = call_structured(be, [{"role": "user", "content": "x"}], ReportAnalysis, task="t")
    assert res.repaired and res.value.category == "alagamento" and len(be.calls) == 2
    repair_msgs = be.calls[1]["messages"]
    assert repair_msgs[-2]["role"] == "assistant" and "validação" in repair_msgs[-1]["content"]


def test_prose_answer_fails_after_repair():
    be = ScriptedBackend(["Desculpe, não posso ajudar.", "Ainda não posso."])
    with pytest.raises(StructuredOutputError):
        call_structured(be, [], ReportAnalysis, task="t")
    assert len(be.calls) == 2  # 1 tentativa + 1 reparo, sem loop infinito


def test_confidence_out_of_range_is_rejected(good_analysis_json):
    bad = good_analysis_json.replace('"confidence": 0.9', '"confidence": 7')
    be = ScriptedBackend([bad, bad])
    with pytest.raises(StructuredOutputError):
        call_structured(be, [], ReportAnalysis, task="t")


def test_schema_mode_downgrades_when_provider_rejects(good_analysis_json, tmp_path):
    audit = AuditLog(tmp_path / "a.jsonl")
    be = ScriptedBackend([LLMBadRequest("response_format unsupported"), LLMBadRequest("json_object unsupported"), good_analysis_json])
    res = call_structured(be, [], ReportAnalysis, task="t", audit=audit)
    assert [c["mode"] for c in be.calls] == ["json_schema", "json_object", "prompt_only"]
    assert res.response_mode == "prompt_only"
    assert [e["to_mode"] for e in audit.events if e["event"] == "mode_downgrade"] == ["json_object", "prompt_only"]


def test_bad_request_on_last_mode_raises():
    be = ScriptedBackend([LLMBadRequest("x")] * 3)
    with pytest.raises(StructuredOutputError):
        call_structured(be, [], ReportAnalysis, task="t")


def test_network_errors_propagate_to_caller():
    with pytest.raises(LLMUnavailable):
        call_structured(ScriptedBackend([LLMUnavailable("down")]), [], ReportAnalysis, task="t")


def test_extract_json_truncated():
    with pytest.raises(ValueError):
        extract_json('{"a": 1, "b": ')
    assert extract_json('texto {"a": "x}y"} fim') == {"a": "x}y"}


def test_strict_schema_shape():
    s = to_strict_schema(ReportAnalysis)
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    assert "minimum" not in str(s) and "maxLength" not in str(s)
    assert s["properties"]["category"]["enum"][0] == "alagamento"


def test_extra_fields_are_ignored_and_long_text_trimmed():
    v = DuplicateVerdict.model_validate({"same_occurrence": True, "confidence": 1, "reason": "x" * 500, "extra": 1})
    assert len(v.reason) == 200


def test_strict_schema_keeps_field_named_title():
    """Regressão: o filtro de keywords removia o campo `title` do ClusterSummary."""
    for model in (ReportAnalysis, DuplicateVerdict, ClusterSummary):
        s = to_strict_schema(model)
        assert set(s["properties"]) == set(model.model_fields), model
        assert set(s["required"]) == set(model.model_fields), model
    assert "title" in to_strict_schema(ClusterSummary)["properties"]
