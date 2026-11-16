from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .ai import AIService
from .audit import AuditLog
from .config import LLMConfig, load_dotenv
from .llm_client import LLMAuthError, OpenRouterClient
from .mock_backend import MockBackend
from .normalize import TZ
from .pipeline import run_pipeline, write_outputs
from .privacy import redact_pii

ROOT = Path(__file__).resolve().parent.parent


def build_ai(mode: str, chaos: bool, cfg: LLMConfig, audit: AuditLog) -> tuple[AIService, dict]:
    if mode == "none":
        return AIService(None, audit=audit), {"backend": "none (somente regras)", "model": None}
    if mode == "mock":
        be = MockBackend(chaos=chaos)
        return AIService(be, cfg.response_mode, audit), {"backend": f"mock{' + chaos' if chaos else ''} (SIMULADO)", "model": "mock/simulado"}
    try:
        be = OpenRouterClient(cfg)
    except LLMAuthError as exc:
        sys.exit(f"Erro: {exc}\nDica: use --llm mock para rodar sem chave, ou --llm none para só regras.")
    return AIService(be, cfg.response_mode, audit), {"backend": "openrouter", **{k: v for k, v in cfg.describe().items() if k != "api_key_set"}}


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="citizentech_ai", description="Fila de priorização de ocorrências urbanas (IA Generativa + regras).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="roda o pipeline completo")
    run.add_argument("--llm", choices=["openrouter", "mock", "none"], default="openrouter")
    run.add_argument("--chaos", action="store_true", help="(mock) injeta falhas para demonstrar o tratamento de erros")
    run.add_argument("--data", default=str(ROOT / "data"))
    run.add_argument("--out", default=str(ROOT / "output"))
    run.add_argument("--as-of", help="data de referência YYYY-MM-DD (padrão: agora)")
    run.add_argument("--model", help="sobrescreve OPENROUTER_MODEL")

    an = sub.add_parser("analyze", help="analisa um único texto e mostra o JSON validado")
    an.add_argument("text")
    an.add_argument("--llm", choices=["openrouter", "mock"], default="openrouter")
    an.add_argument("--model")

    sub.add_parser("config", help="mostra a configuração ativa (sem segredos)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    cfg = LLMConfig.from_env()
    if getattr(args, "model", None):
        cfg = LLMConfig(**{**cfg.__dict__, "model": args.model})

    if args.cmd == "config":
        print(json.dumps(cfg.describe(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "analyze":
        from .models import Report as R

        audit = AuditLog(None)
        ai, _ = build_ai(args.llm, False, cfg, audit)
        r = R("CLI-1", "cli", datetime.now(TZ), redact_pii(args.text), None, None, None, "unresolved")
        a = ai.analyze_report(r)
        print(json.dumps(a.__dict__, indent=2, ensure_ascii=False))
        return 0

    as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=TZ) if args.as_of else datetime.now(TZ)
    audit = AuditLog(Path(args.out) / "llm_audit.jsonl")
    ai, info = build_ai(args.llm, args.chaos, cfg, audit)
    res = run_pipeline(args.data, ai, as_of)
    jp, mp = write_outputs(res, as_of, info, args.out)
    print(f"\nOK -> {jp}\n     {mp}\nestatísticas: {json.dumps(res.stats, ensure_ascii=False)}")
    for i, o in enumerate(res.queue[:5], start=1):
        print(f"  {i}. [{o.band:7}] {o.score:5.1f}  {o.category:15} {len(o.reports):2} relato(s)  {o.address}")
    return 0
