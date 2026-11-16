"""Imprime o prompt montado para um relato."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from citizentech_ai import prompts  # noqa: E402

text = "Rua completamente alagada na frente da escola, a água está subindo e os carros não conseguem passar. Meu telefone é [TELEFONE] se precisarem."
for m in prompts.analyze_messages(text, "app", None, "alagamento"):
    print(f"--- {m['role']} ---\n{m['content']}\n")
