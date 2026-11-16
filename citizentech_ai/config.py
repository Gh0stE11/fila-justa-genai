"""Configuração do cliente LLM, lida de variáveis de ambiente ou do .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Carrega o .env sem sobrescrever variáveis já definidas."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim"}


@dataclass(frozen=True)
class LLMConfig:
    api_key: str | None = None
    model: str = "openai/gpt-4o-mini"
    fallback_models: tuple[str, ...] = ()
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.0
    max_tokens: int = 600
    timeout_s: float = 30.0
    max_retries: int = 2
    backoff_s: float = 1.0
    response_mode: str = "json_schema"  # json_schema | json_object | prompt_only
    require_parameters: bool = False
    app_url: str = "https://github.com/citizentech/fila-priorizacao"
    app_title: str = "CitizenTech - Fila de Priorizacao"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        fallbacks = tuple(
            m.strip() for m in os.getenv("OPENROUTER_FALLBACK_MODELS", "").split(",") if m.strip()
        )
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            model=os.getenv("OPENROUTER_MODEL", cls.model),
            fallback_models=fallbacks,
            base_url=os.getenv("OPENROUTER_BASE_URL", cls.base_url),
            temperature=float(os.getenv("LLM_TEMPERATURE", cls.temperature)),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", cls.max_tokens)),
            timeout_s=float(os.getenv("LLM_TIMEOUT_S", cls.timeout_s)),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", cls.max_retries)),
            response_mode=os.getenv("LLM_RESPONSE_MODE", cls.response_mode),
            require_parameters=_bool(os.getenv("LLM_REQUIRE_PARAMETERS"), False),
        )

    def describe(self) -> dict:
        """Configuração sem a chave, segura para log."""
        return {
            "model": self.model,
            "fallback_models": list(self.fallback_models),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "response_mode": self.response_mode,
            "require_parameters": self.require_parameters,
            "api_key_set": bool(self.api_key),
        }
