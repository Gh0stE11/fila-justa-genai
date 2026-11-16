"""Classificação por palavras-chave. Usada quando o LLM falha e pelo backend simulado."""
from __future__ import annotations

import re

from .privacy import fold
from .schemas import ReportAnalysis

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("arvore_caida", ("arvore", "galho")),
    ("bueiro_entupido", ("bueiro", "boca de lobo", "entupid")),
    ("alagamento", ("alag", "enchente", "inunda", "agua subindo", "agua acumulada", "transbord")),
    ("buraco", ("buraco", "cratera", "afundou", "afundamento")),
    ("iluminacao", ("poste", "lampada", "sem luz", "escuro", "iluminacao")),
    ("lixo", ("lixo", "entulho", "descarte")),
]
RISK = ("risco de morte", "choque", "fio solto", "fio caido", "correnteza", "afog", "moto caiu", "caiu de moto", "desabar", "desabando", "pessoa presa")
BLOCK = ("bloqueando", "bloqueou", "bloqueada", "interditad", "nao passa", "ninguem passa", "nao conseguem passar", "nao consegue passar", "nao conseguem sair", "impede", "fechou a rua", "tomando a rua", "sem passagem", "ambulancia nao")
VULNERABLE = ("crianca", "idos", "cadeirante", "gestante", "escola", "creche", "bebe")


def _find(text_folded: str, original: str, needles: tuple[str, ...]) -> str | None:
    for n in needles:
        idx = text_folded.find(n)
        if idx != -1:
            end = idx + len(n)
            while end < len(original) and original[end].isalpha():
                end += 1
            start = idx
            while start > 0 and original[start - 1].isalpha():
                start -= 1
            return original[start:end]
    return None


def classify_by_rules(text: str, hint_category: str | None = None) -> ReportAnalysis:
    f = fold(text)
    category = "nao_acionavel"
    for cat, needles in CATEGORY_KEYWORDS:
        if any(n in f for n in needles):
            category = cat
            break
    if category == "nao_acionavel" and hint_category and hint_category != "outros":
        category = hint_category
    evidence: list[str] = []
    flags = {}
    for name, needles in (("risk_to_life", RISK), ("blocks_passage", BLOCK), ("vulnerable_people", VULNERABLE)):
        hit = _find(f, text, needles)
        flags[name] = hit is not None
        if hit and hit not in evidence:
            evidence.append(hit)
    first = re.split(r"(?<=[.!?])\s+", text.strip())[0] if text.strip() else ""
    return ReportAnalysis(
        category=category,
        confidence=0.55 if category != "nao_acionavel" else 0.4,
        summary=first,
        evidence=evidence,
        **flags,
    )
