#!/usr/bin/env python3
"""env_loader.py — charge `<racine>/.env` dans `os.environ`, sans dépendance (stdlib seule).

Tous les paramètres du projet sont centralisés dans `.env` (gitignoré : secrets, chemins locaux)
avec `.env.example` versionné comme modèle documenté. Règle : une variable déjà définie dans
l'environnement réel du shell prime toujours sur `.env` (sauf `override=True`) ; une valeur vide
dans `.env` ne définit rien.

Usage (en tête de point d'entrée, avant toute lecture de `os.environ`) :
    from env_loader import load_dotenv
    load_dotenv()
"""
from __future__ import annotations

import os

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_PATH = os.path.join(ROOT, ".env")


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse un texte dotenv : lignes `KEY=VALUE`, `export KEY=VALUE`, guillemets simples/doubles,
    commentaires `#` (pleine ligne ou en fin de valeur non quotée)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].strip()
        if key:
            out[key] = val
    return out


def load_dotenv(path: str | None = None, override: bool = False) -> dict[str, str]:
    """Charge le fichier `.env` dans `os.environ` et retourne les clés parsées.
    `override=False` (défaut) : ne remplace jamais une variable déjà présente dans l'environnement."""
    env_path = path or DEFAULT_ENV_PATH
    loaded: dict[str, str] = {}
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as fh:
            loaded = parse_dotenv(fh.read())
        for key, val in loaded.items():
            if val and (override or key not in os.environ):
                os.environ[key] = val
    return loaded


def load_studio_env(env_path: str = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Alias rétrocompatible de `load_dotenv` (utilisé historiquement par fetch_samples_api)."""
    return load_dotenv(env_path)


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(load_dotenv(sys.argv[1] if len(sys.argv) > 1 else None), indent=2, ensure_ascii=False))
