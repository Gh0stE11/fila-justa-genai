from pathlib import Path

from citizentech_ai.normalize import Gazetteer, load_all, normalize_address
from citizentech_ai.privacy import fold, redact_pii

DATA = Path(__file__).resolve().parent.parent / "data"


def test_redacts_phone_cpf_email():
    t = redact_pii("ligue (11) 98765-4321 ou +55 11 91234-5678, cpf 123.456.789-09, a@b.com")
    assert "98765" not in t and "91234" not in t and "123.456" not in t and "a@b.com" not in t
    assert "[TELEFONE]" in t and "[CPF]" in t and "[EMAIL]" in t


def test_redaction_keeps_street_numbers_and_dates():
    assert redact_pii("Rua das Acácias, 120 em 07/11/2026") == "Rua das Acácias, 120 em 07/11/2026"


def test_fold_preserves_length():
    s = "Ação Ônibus Ipê ç"
    assert len(fold(s)) == len(s) and fold(s) == "acao onibus ipe c"


def test_address_normalization_variants_match():
    assert normalize_address("R. das Acácias, 100") == normalize_address("Rua das Acacias 100")
    assert normalize_address("Av. Brasil Nova, 500") == "avenida brasil nova 500"


def test_gazetteer_exact_street_level_and_miss():
    g = Gazetteer(DATA / "gazetteer.json")
    assert g.resolve("R. das Acácias, 120")[2] == "gazetteer"
    assert g.resolve("Rua das Acácias, 999")[2] == "gazetteer_street"
    assert g.resolve("Rua Inexistente, 99") is None and g.resolve("") is None


def test_load_all_unifies_three_channels_without_personal_data():
    reports = load_all(DATA)
    assert len(reports) == 26 and {r.channel for r in reports} == {"app", "whatsapp", "central_156"}
    blob = " ".join(r.text for r in reports)
    for leaked in ("98765-4321", "maria.silva@exemplo.com"):
        assert leaked not in blob
    assert all(r.received_at.tzinfo is not None for r in reports)
    assert any(r.loc_source == "unresolved" for r in reports) and any(r.loc_source == "gps" for r in reports)
    assert [r.received_at for r in reports] == sorted(r.received_at for r in reports)
    assert not any("+55" in r.text for r in reports)  # telefone do remetente nunca entra
