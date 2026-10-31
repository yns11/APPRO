"""The engine guide (docs/guide_moteur.md) is generated from the engine : it must be regenerated
whenever the engine or the generator changes (``python scripts/guide_moteur.py``)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]


def test_le_guide_du_moteur_est_a_jour(capsys):
    spec = importlib.util.spec_from_file_location("guide_moteur", RACINE / "scripts" / "guide_moteur.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    expected = (RACINE / "docs" / "guide_moteur.md").read_text(encoding="utf-8")
    assert module.guide() == expected, "docs/guide_moteur.md n'est plus à jour : python scripts/guide_moteur.py"
    assert "## 15. Récapitulatif des paramètres" in expected
    _ = sys
