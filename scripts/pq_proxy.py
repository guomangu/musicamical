"""v4.1 P4 — proxy perceptuel PQ rapide (régresseur sklearn, < 10 ms, 0 GPU, 0 token LLM).

En attendant un scoring neuronal systématique, un petit régresseur (Ridge, fallback
GradientBoosting si assez d'échantillons) est entraîné sur le vecteur
`(métriques DSP + LRA + crest + MelodicAppealIndex + ratio de mutation)` des morceaux
déjà évalués en PQ (dataset JSONL, ex. `memory/pq_dataset.jsonl`, une ligne
`{"features": {...}, "pq": 8.05, "project": "..."}` par morceau). `predict()` renvoie le
PQ estimé en < 10 ms (utile dans le tournoi de variantes et en CI) ; sans modèle chargé,
`predict()` dégrade proprement en `None` (jamais d'exception).

Usage :
  .venv/bin/python scripts/pq_proxy.py train --dataset memory/pq_dataset.jsonl --out models/pq_proxy.joblib
  .venv/bin/python scripts/pq_proxy.py predict --model models/pq_proxy.joblib --features '{"lufs": -11.0, ...}'
  .venv/bin/python scripts/pq_proxy.py record --dataset memory/pq_dataset.jsonl --pq 8.05 --features '{...}'
  .venv/bin/python scripts/pq_proxy.py check --model models/pq_proxy.joblib   # CI : modèle chargeable + predict < 10 ms
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATASET = os.path.join(ROOT, "memory", "pq_dataset.jsonl")
DEFAULT_MODEL = os.path.join(ROOT, "models", "pq_proxy.joblib")

_LAST_PREDICT_MS: float = 0.0

FEATURE_KEYS = (
    "lufs", "lra_lu", "crest_factor_db", "pink_slope_db_per_oct",
    "low_end_share", "low_mids_share", "air_share", "mid_high_side_to_mid_db",
    "corridor_score", "spectral_match", "dynamic_arc_db", "stereo_width_db",
    "stereo_balance_db", "sharpness", "true_peak_dbtp", "min_short_term_plr_db",
    "melodic_appeal_index", "mutation_ratio", "lead_vs_harmony_db", "mono_folddown_loss_db",
)


def features_to_vector(features: dict) -> list[float]:
    """Vecteur fixe (len(FEATURE_KEYS)) ; clés absentes -> 0.0 (imputation déterministe)."""
    return [float(features.get(k, 0.0) or 0.0) for k in FEATURE_KEYS]


def load_dataset(path: str) -> list[tuple[list[float], float, str]]:
    rows: list[tuple[list[float], float, str]] = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            feats = rec.get("features")
            pq = rec.get("pq")
            if isinstance(feats, dict) and isinstance(pq, (int, float)):
                rows.append((features_to_vector(feats), float(pq), str(rec.get("project", ""))))
    return rows


class PQProxy:
    """Régresseur PQ léger. `predict` -> float, ou None si non entraîné (dégradation propre)."""

    def __init__(self, model=None, trained: bool = False, n_samples: int = 0) -> None:
        self.model = model
        self.trained = bool(trained and model is not None)
        self.n_samples = int(n_samples)

    @classmethod
    def train(cls, dataset_path: str) -> "PQProxy":
        rows = load_dataset(dataset_path)
        if len(rows) < 5:
            return cls(None, False, len(rows))
        from sklearn.linear_model import Ridge
        X = [r[0] for r in rows]
        y = [r[1] for r in rows]
        model = Ridge(alpha=1.0)
        model.fit(X, y)
        return cls(model, True, len(rows))

    def predict(self, features: dict) -> float | None:
        if not self.trained or self.model is None:
            return None
        t0 = time.perf_counter()
        val = float(self.model.predict([features_to_vector(features)])[0])
        global _LAST_PREDICT_MS
        _LAST_PREDICT_MS = (time.perf_counter() - t0) * 1000.0
        return round(max(0.0, min(10.0, val)), 3)

    def save(self, path: str) -> None:
        import joblib
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        joblib.dump({"model": self.model, "trained": self.trained, "n_samples": self.n_samples,
                     "feature_keys": FEATURE_KEYS}, path)

    @classmethod
    def load(cls, path: str) -> "PQProxy":
        if not os.path.isfile(path):
            return cls(None, False, 0)
        import joblib
        blob = joblib.load(path)
        return cls(blob.get("model"), bool(blob.get("trained")), int(blob.get("n_samples", 0)))


def append_record(dataset_path: str, features: dict, pq: float, project: str = "") -> int:
    os.makedirs(os.path.dirname(os.path.abspath(dataset_path)), exist_ok=True)
    with open(dataset_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"features": features, "pq": float(pq), "project": project,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                            ensure_ascii=False) + "\n")
    return len(load_dataset(dataset_path))


def check_model(path: str, budget_ms: float = 10.0) -> list[str]:
    """CI : le modèle se charge et prédit en < budget_ms (échantillon synthétique)."""
    errors: list[str] = []
    proxy = PQProxy.load(path)
    if not os.path.isfile(path):
        return [f"absent: {path}"]
    if not proxy.trained:
        errors.append("modèle non entraîné")
    sample = {k: 0.0 for k in FEATURE_KEYS}
    sample["lufs"] = -11.0
    t0 = time.perf_counter()
    val = proxy.predict(sample)
    ms = (time.perf_counter() - t0) * 1000.0
    if val is None:
        errors.append("predict -> None sur modèle chargé")
    if ms > budget_ms:
        errors.append(f"predict trop lent: {ms:.2f} ms > {budget_ms} ms")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Proxy PQ sklearn (< 10 ms, 0 GPU) entraîné sur vos rendus évalués")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("train", "predict", "record", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--dataset", default=DEFAULT_DATASET)
        sp.add_argument("--model", default=DEFAULT_MODEL)
        sp.add_argument("--features", default="{}")
        sp.add_argument("--pq", type=float, default=0.0)
        sp.add_argument("--project", default="")
    args = ap.parse_args()
    if args.cmd == "train":
        proxy = PQProxy.train(args.dataset)
        if not proxy.trained:
            print(f"dataset insuffisant (< 5 lignes) dans {args.dataset} — modèle non sauvegardé", file=sys.stderr)
            return 2
        proxy.save(args.model)
        print(f"modèle PQ entraîné ({proxy.n_samples} échantillons) -> {args.model}")
        return 0
    if args.cmd == "record":
        n = append_record(args.dataset, json.loads(args.features), args.pq, args.project)
        print(f"enregistrement ajouté ({n} lignes dans {args.dataset})")
        return 0
    if args.cmd == "predict":
        proxy = PQProxy.load(args.model)
        val = proxy.predict(json.loads(args.features))
        print("None" if val is None else val)
        return 0 if val is not None else 3
    if args.cmd == "check":
        errors = check_model(args.model)
        for e in errors:
            print("ERREUR:", e)
        print("OK" if not errors else "ÉCHEC")
        return 0 if not errors else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
