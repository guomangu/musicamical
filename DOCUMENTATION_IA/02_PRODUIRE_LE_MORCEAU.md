# 02 — Produire le morceau (façade MCP `ai-studio-prod`, exactement 3 outils)

Budget façade : schémas + runbook V4 (≤ 750 tokens) + retour prepare (≤ 450 cars) **< 1 800 tokens combinés** (testé en CI). Un morceau complet = **~120–220 tokens** de `strudel_code` + 2–3 appels d'outils.

## Les 3 outils (signatures exactes)

```python
studio_prepare(project_dir: str, intent: str = "", refs: list[str] | None = None,
               remix: str | None = None, lyrics: dict | None = None) -> str   # ≤ 450 cars, tabulaire

studio_compose(project_dir: str, strudel_code: str, palette_id: str = "auto",
               auto_master: bool = True, auto_prepare: bool = False,
               remix_source_path: str | None = None, lyrics_spec: dict | None = None) -> str  # ≤ 400 cars

studio_refine(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
              semantic_patch: dict | None = None, export_mp3: bool = True) -> str           # ≤ 400 cars
```

## Scénarios

| Scénario | Quand | Appel |
|---|---|---|
| **A — Création originale** (défaut) | nouveau morceau | `studio_compose(projet, strudel_code, auto_prepare=true)` — **1 tour**, palette `auto` = synthés natifs `gl.*` (0 token). `auto_prepare` génère un blueprint **valide schéma** (pistes depuis les `$`, `chord_progression` depuis `grid()`, `motif_cell.apex` selon valence) |
| **B — Samples verrouillés** | dossier `Samples/` fourni | `studio_prepare(projet, intent, refs=[...])` d'abord (palettes depuis les samples indexés), puis compose |
| **C — Remix** | audio source fourni | `studio_compose(..., remix_source_path="source.wav")` (ou `studio_prepare(..., remix=...)`) |
| **D — Chant** | paroles fournies | `lyrics_spec={...}` → piste `$vocals` synchronisée dans le blueprint |

`studio_prepare` sans `Samples/` projet retourne `PALETTES:` vide : c'est normal, le chemin par défaut est `palette_id="auto"` + `gl.*` ; le `CTX` du retour (132 BPM / C minor…) est une **valeur par défaut à surcharger** dans `@meta`.

## Ce que retourne `studio_compose`

- Texte ≤ 400 cars : `OK tracks=… notes=… seed=… guards=… eval=… fc=… gate=PASS/FAIL gf=… sw=… rest=… | handoff=handoff.txt`.
- `<projet>/handoff.txt` (≤ 350 tokens) : `strudel=composition.strudel#<sha> seed=…`, `bridge_ok`, `sections`, `guards`, **`preflight=PASS/FAIL gf= sw= rest= bad_leaps=`**, artefacts (`score_ir.json`, `arrangement_plan.json`, `composition.strudel.haps.json`), `master=renders/master.wav` si Gridline présent.
- Si `gate=FAIL` : voir [01 §4](01_COMPOSER_AVEC_STRUDEL.md) — corriger le motif Strudel, vérifier en 0 rendu avec `preflight_strudel_gate.py`, re-composer. **Ne jamais corriger le mix en touchant aux faders** (voir ci-dessous).

## Retouches (tour 3)

```python
studio_refine(projet, semantic_patch={"lead": {"oct": -1, "rests": "+15%", "cutoff": 3200}})   # ~20-40 tokens
studio_refine(projet, semantic_patch={"section": "chorus", "ops": {"energy": "+2dB"}})
studio_refine(projet, strudel_patch={"replace_track": "lead", "code": "deg(...)..."})        # réécriture piste
studio_refine(projet, delta_json={...})                                                        # ajustements mix fins
```

## Qualité du mix (déjà câblée, 0 token pour l'agent)

- **`math_mix_solver.py`** calcule en 1 passe (< 0.8 s, 0 rendu supplémentaire) gains/EQ/pan/largeur/sends/limiteur depuis les stems ; il en est le **propriétaire unique** (`mark_solver_applied` → `pipeline_state.json` ; l'autofix transactionnel ne garde que les sécurités Palier 1 : `SWAP_SAMPLE`, `INVERT_808_POLARITY`, `SHIFT_BASS_MICRO_MS`, `TRIM_SAMPLE_START_MS`, `FIX_TRACK_ROOT_NOTE` — verdict `SKIPPED_SOLVER_OWNERSHIP` sinon).
- Micro-passe scalaire résiduelle `scalar_master_fix` : Master Gain si |LUFS−target| > 0.7 dB, Ceiling si TP > −1.0 dBTP (jamais d'EQ/fader piste).
- Calibration réelle `gl.*` : `config/gl_calibration.json` (régénérer 1×/version Gridline avec `scripts/calibrate_gl_plugins.py`) lue par le solveur.
- Corridors spectraux : `golden_references/spectral_corridors_external.json` (vrais masters commerciaux, provenance `external_pro_reference`, généré par `scripts/build_external_corridors.py`) préféré automatiquement au fichier interne.
- Proxy PQ : `scripts/pq_proxy.py` (régresseur sklearn, < 10 ms, `predict()` → `None` si non entraîné).
- Seuils durs du master : ceiling limiteur `-1.2 dBTP`, jamais de LPF brique < 6.5 kHz (harmonie) / 9 kHz (lead) — tout est dans `config/mix_profiles.json`.

## Qualité des samples (Audio Intelligence v2)

À l'indexation (`index_samples.py`), chaque sample reçoit un profil v2 (`sample_quality_v2`) : true peak ×4, faux WAV (upsamplé lossy 11/16 kHz), SNR, DC → `REJECTED` exclu de la sélection ; morphologie : signature 808 (pitch drop), T30, saturation, rôle, pitch MIDI, bark24 ; la sélection est hybride SQL + vecteur avec pénalité de masquage Bark24 ; `audit_project_sources.py` rapporte l'alignement de phase Kick/808 (`PHASE_KICK_BASS`). Détails : [docs/08](../docs/08_AUDIO_INTELLIGENCE_V2.md).

## Commandes utiles en 0 rendu

```bash
node scripts/strudel_lint.mjs <projet>/composition.strudel        # lint (≤ 2 lignes ERR)
.venv/bin/python scripts/preflight_strudel_gate.py <projet>        # gate MIDI simulé (PASS/FAIL + bad_leaps)
.venv/bin/python scripts/sample_quality_v2.py <fichier.wav> --selftest
```
