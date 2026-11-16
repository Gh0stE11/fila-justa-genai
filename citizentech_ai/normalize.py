"""Converte os três canais em uma única estrutura `Report`."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Report
from .privacy import fold, redact_pii

TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo (sem horário de verão)
APP_CATEGORY = {
    "ALAG": "alagamento", "BURACO": "buraco", "BUEIRO": "bueiro_entupido", "ARVORE": "arvore_caida",
    "ILUM": "iluminacao", "LIXO": "lixo", "OUTRO": "outros",
}
_ABBREV = {"r": "rua", "av": "avenida", "al": "alameda", "tv": "travessa", "pca": "praca", "est": "estrada"}


def normalize_address(addr: str) -> str:
    s = re.sub(r"[.,;]", " ", fold(addr))
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split(" ")
    if parts and parts[0] in _ABBREV:
        parts[0] = _ABBREV[parts[0]]
    return " ".join(parts)


class Gazetteer:
    """Dicionário endereço -> coordenada, no lugar de um geocoder."""

    def __init__(self, path: str | Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.table = {normalize_address(k): (v["lat"], v["lon"]) for k, v in raw.items()}

    def resolve(self, address: str | None) -> tuple[float, float, str] | None:
        if not address:
            return None
        key = normalize_address(address)
        if key in self.table:
            return (*self.table[key], "gazetteer")
        street = re.sub(r"\s+\d+\w*$", "", key)
        if street in self.table:
            return (*self.table[street], "gazetteer_street")
        return None


def _located(rid, channel, when, text, address, gaz, lat=None, lon=None, hint=None) -> Report:
    if lat is not None and lon is not None:
        src = "gps"
    else:
        hit = gaz.resolve(address)
        lat, lon, src = (hit[0], hit[1], hit[2]) if hit else (None, None, "unresolved")
    return Report(rid, channel, when, redact_pii(text.strip()), address, lat, lon, src, hint)


def load_app(path: Path, gaz: Gazetteer) -> list[Report]:
    out = []
    for r in json.loads(path.read_text(encoding="utf-8")):
        loc = r.get("location") or {}
        out.append(_located(
            f"APP-{r['report_id']}", "app", datetime.fromisoformat(r["created_at"]), r["description"],
            None, gaz, loc.get("lat"), loc.get("lon"), APP_CATEGORY.get(r.get("category_code", ""), None),
        ))
    return out


def load_whatsapp(path: Path, gaz: Gazetteer) -> list[Report]:
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            when = datetime.strptime(row["timestamp"], "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
            # o telefone (sender) não é lido, por minimização de dados
            out.append(_located(f"WPP-{i:03d}", "whatsapp", when, row["message"], row["local"], gaz))
    return out


def load_central_156(path: Path, gaz: Gazetteer) -> list[Report]:
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            when = datetime.fromisoformat(f"{row['data_abertura']}T{row['hora']}").replace(tzinfo=TZ)
            addr = f"{row['logradouro']}, {row['numero']}".strip(", ")
            out.append(_located(f"156-{row['protocolo']}", "central_156", when, row["descricao"], addr, gaz))
    return out


def load_all(data_dir: str | Path) -> list[Report]:
    d = Path(data_dir)
    gaz = Gazetteer(d / "gazetteer.json")
    reports = [
        *load_app(d / "sources" / "app_reports.json", gaz),
        *load_whatsapp(d / "sources" / "whatsapp_export.csv", gaz),
        *load_central_156(d / "sources" / "central_156.csv", gaz),
    ]
    return sorted(reports, key=lambda r: r.received_at)
