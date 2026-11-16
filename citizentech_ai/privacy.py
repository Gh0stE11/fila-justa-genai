"""Remove dados pessoais antes de o texto ir para o LLM ou para a saída (LGPD)."""
from __future__ import annotations

import re
import unicodedata

_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?55[\s-]?)?(?:\(?\d{2}\)?[\s-]?)?(?:9[\s-]?)?\d{4}[\s-]?\d{4}(?!\d)")


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CPF.sub("[CPF]", text)
    text = _PHONE.sub("[TELEFONE]", text)
    return text


def fold(s: str) -> str:
    """Minúsculas sem acento, mantendo o tamanho do texto original."""
    return "".join(unicodedata.normalize("NFKD", c)[0] for c in s).lower()
