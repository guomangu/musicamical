"""Garde-fous de la configuration `.env` / `.env.example` : .env gitignoré (secrets),
.env.example versionné et complet (toute clé lue par le code y est documentée),
aucun secret ni chemin /home dans .env.example, chargeur scripts/env_loader.py sans dépendance."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import env_loader  # noqa: E402

# Variables système lues par le code mais hors périmètre .env (fournies par l'OS)
SYSTEM_KEYS = {"DISPLAY", "WAYLAND_DISPLAY", "PATH", "HOME", "LANG", "LC_ALL", "TERM", "SHELL",
               "XDG_RUNTIME_DIR", "USER", "PWD", "OLDPWD", "SHLVL", "_", "PYTHONPATH", "VIRTUAL_ENV",
               "HF_HUB_OFFLINE", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}

ENV_KEY_RE = re.compile(r'os\.(?:environ\.get|getenv)\(\s*"([A-Z_0-9]+)"')


def _code_env_keys() -> set[str]:
    keys: set[str] = set()
    for base in (SCRIPTS, os.path.join(ROOT, "mcp-servers")):
        for dirpath, dirnames, fns in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py") or fn.startswith("test_"):
                    continue
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="ignore") as fh:
                    keys.update(ENV_KEY_RE.findall(fh.read()))
    return keys


class TestEnvFiles(unittest.TestCase):
    def test_env_example_exists_and_env_is_gitignored(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".env.example")))
        rc = subprocess.run(["git", "-C", ROOT, "check-ignore", ".env"],
                            capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, ".env doit être gitignoré (secrets)")
        self.assertIn(".env", rc.stdout.strip())

    def test_env_example_has_no_secrets_no_home(self):
        with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("/home/", text)
        for line in text.splitlines():
            m = re.match(r"^\s*#?\s*([A-Z_0-9]*(?:KEY|TOKEN|SECRET)[A-Z_0-9]*)\s*=\s*(\S.*)$", line)
            if m:
                self.assertLess(len(m.group(2)), 16, f"valeur ressemblant à un secret dans .env.example: {m.group(1)}")

    def test_all_env_keys_used_in_code_are_documented(self):
        with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as fh:
            example = fh.read()
        documented = set(re.findall(r"^\s*#?\s*([A-Z_0-9]+)\s*=", example, flags=re.M))
        used = _code_env_keys() - SYSTEM_KEYS
        missing = sorted(k for k in used if k not in documented)
        self.assertEqual(missing, [], f"clés env utilisées par le code mais absentes de .env.example : {missing}")

    def test_parse_dotenv(self):
        text = (
            "# commentaire\n"
            "\n"
            "AI_STUDIO_DIR=\n"
            "export FREESOUND_API_KEY=\"abc123\"\n"
            "MIX_PROFILES_PATH=/tmp/x.json # commentaire inline\n"
            "QUOTED='valeur avec espaces'\n"
            "BAD LINE SANS EGAL\n"
        )
        parsed = env_loader.parse_dotenv(text)
        self.assertEqual(parsed["AI_STUDIO_DIR"], "")
        self.assertEqual(parsed["FREESOUND_API_KEY"], "abc123")
        self.assertEqual(parsed["MIX_PROFILES_PATH"], "/tmp/x.json")
        self.assertEqual(parsed["QUOTED"], "valeur avec espaces")
        self.assertNotIn("BAD LINE SANS EGAL", parsed)

    def test_load_dotenv_does_not_override_real_env_and_ignores_empty(self):
        with tempfile.TemporaryDirectory() as td:
            env_file = os.path.join(td, ".env")
            with open(env_file, "w", encoding="utf-8") as fh:
                fh.write("TEST_ENV_KEY_93D59D=from_dotenv\nEMPTY_KEY_93D59D=\n")
            os.environ["TEST_ENV_KEY_93D59D"] = "from_real_env"
            try:
                loaded = env_loader.load_dotenv(env_file)
                self.assertEqual(loaded["TEST_ENV_KEY_93D59D"], "from_dotenv")
                self.assertEqual(os.environ["TEST_ENV_KEY_93D59D"], "from_real_env")  # env réel prime
                self.assertNotIn("EMPTY_KEY_93D59D", os.environ)  # valeur vide = non défini
                # override=True force
                env_loader.load_dotenv(env_file, override=True)
                self.assertEqual(os.environ["TEST_ENV_KEY_93D59D"], "from_dotenv")
            finally:
                os.environ.pop("TEST_ENV_KEY_93D59D", None)
                os.environ.pop("EMPTY_KEY_93D59D", None)

    def test_load_dotenv_missing_file_is_noop(self):
        self.assertEqual(env_loader.load_dotenv("/nonexistent/.env"), {})


if __name__ == "__main__":
    unittest.main()
