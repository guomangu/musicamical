# RETEX — Session RAP FR 2026-11-25 (master.wav 3 min 02 s, 44,1 kHz stéréo, livré OK)

Retour d'expérience d'une session de production réelle via la façade v4.0. sert de
**checklist anti-erreurs** : chaque point a coûté un essai-erreur à l'époque, il est
maintenant corrigé dans le dépôt + testé en CI. Runbook : [AGENT_RUNBOOK_V4.md](AGENT_RUNBOOK_V4.md).

## 1. Bugs bloquants rencontrés (corrigés + tests de régression dans `tests/test_session_bugs.py`)

| Fichier | Bug | Symptôme | Fix |
| :--- | :--- | :--- | :--- |
| `scripts/compile_song.py` (help) | `%` non échappé dans une help argparse | `ValueError`/`TypeError` au `--help` (py3.14/3.12) | « 100 pourcent » ; test statique anti-`%` sur tous les `help=` |
| `scripts/target_fingerprint.py` (help) | idem (`60% `) | idem | idem |
| `mcp-servers/audio-analyzer/deep_diagnostics.py` | `issues` référencé avant `issues = []` ; `L`/`R` indéfinis dans le détail mono | `UnboundLocalError` / `NameError` sur métrique hors seuil | déclaration en tête de `run_deep_audit` ; test sur WAV synthétique à PLR faible |
| `scripts/render_and_audit.py` | `_tp_limit` défini dans `render_and_evaluate` mais lu dans `_apply_autofix_mutations` | `NameError` dès qu'un autofix limiteur se déclenche | recalcul local via `limiter_invariants()` + `get_invariant()` ; test AST + appel |
| `scripts/strudel_to_score_ir.py` | `--seed 42` forcé par défaut écrasait `seed` du `@strudel-meta` | `handoff.txt` indiquait `seed=42` au lieu de 1337 | le pont ne passe `--seed` que s'il est demandé ; `seed` = `meta.seed` ou SHA-256 du source |

## 2. Blueprint minimal VALIDE (généré automatiquement par `_ensure_blueprint` depuis v4.1)

`tracks` déduits des pistes `$` du Strudel (rôle via `classify_role`, `source_type: "gridline_native"`),
`chord_progression` depuis `grid(...)`, `motif_cell.apex_midi` selon la valence :

```json
{
  "title": "mon_morceau", "genre": "trap_rap", "bpm": 140.0,
  "time_signature": {"numerator": 4, "denominator": 4}, "key": "C",
  "harmonic_core": {"key": "C", "mode": "minor", "progression_bars": 1,
    "chord_progression": ["Cm7", "Abmaj7", "Fm7", "G7"],
    "motif_cell": {"intervals_from_tonic": [0, 3, 7], "rhythm_beats": [0, 2], "apex_midi": 67},
    "phrase_plan": "A-A'-A''-B"},
  "structure": [{"section": "intro", "start_bar": 1, "bars": 4, "energy": 0.4}],
  "tracks": [{"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.warmsynth", "mixer_track": 5}]
}
```

Valide contre `schemas/song_blueprint.schema.json` (`minItems: 1` sur `tracks`, `structure`,
`chord_progression` ; `motif_cell` requis complet).

## 3. Exemple Strudel anti-clone + gate MIDI (passe du 1er coup, testé en CI)

Recettes : variations seedées par piste (`.sometimesBy(0.2-0.3, x => x.degradeBy(0.5))`),
cycles bass/lead de **16 mesures** (jamais 4 mesures 100 % statiques, sinon `clone_8bar` > 0.94),
lead qui résout ses sauts ≥ 5 st en sens inverse par un pas de 1–3 st avec 20–50 % de silences,
basse en sens contraire du lead. L'exemple canonique à jour (0 correction de garde-fou) est dans
[AGENT_RUNBOOK_V4.md](AGENT_RUNBOOK_V4.md) et `assets/composition_trap_example.strudel`.

## 4. Formules & divers utiles

- Durée visée ~3 min : `bars = round(180 * bpm / 240)` (90 BPM → 68 mesures = 3 min 02 s ; 140 BPM → 105).
- `studio_prepare` sans `Samples/` projet renvoie `PALETTES:` vide : normal — utiliser
  `palette_id="auto"` (synthés natifs `gl.*`) ; le `CTX` du retour est une valeur par défaut à
  surcharger dans `@meta` (bpm, key, mode, form).
- Modèles Audiobox/CLAP absents : `render_and_audit.py --no-neural` (fallback auto sur poids manquants).
- Warnings `pyloudnorm` (« resampled to 48 kHz ») : filtrer ou rendre en 48 kHz natif.
- `master.wav` 24-bit ≈ 48 Mo / 3 min : `AI_STUDIO_PRUNE_MASTER_WAV=1` ne garde que `master.mp3` + le rapport.
