# AI-Studio Musical — Gridline + Zed + Antigravity

Studio complet de production musicale assistée par IA sur **Fedora / Ultramarine 44 (PipeWire + Wayland)** dans `$AI_STUDIO_DIR` (accessible aussi via `~/Music/AI-Studio`).

## Documentation pour l'IA de production (lire CECI en priorité pour générer un morceau)
- [**DOCUMENTATION_IA/**](DOCUMENTATION_IA/README.md) — 3 documents compacts et complets (workflow 2 tours, contrat Strudel v4.1 + exemple canonique testé, pre-flight du gate, façade 3 outils `ai-studio-prod`, qualité mix & samples v2). Ne lis PAS les facettes `docs/` en entier pour produire un morceau.

## Documentation (5 Facettes Universelles & Optimisées pour Agents IA)
- **Règles normatives agents IA (Routeur + Machine à états 5 tours)** : [`.rules`](.rules) (liées par `GEMINI.md` et `AGENTS.md`)
- **Facette 1 — Système, Stack, Gridline `0.7.7`, Unités & Guide Utilisateur** : [`docs/01_SYSTEME_STACK_ET_GUIDE.md`](docs/01_SYSTEME_STACK_ET_GUIDE.md)
- **Facette 2 — Composition Universelle, Émotion `(V, T, E, D)`, Harmonie, Mélodie & Rythmes Multi-Genres** : [`docs/02_COMPOSITION_EMOTIONNELLE_ET_THEORIE.md`](docs/02_COMPOSITION_EMOTIONNELLE_ET_THEORIE.md)
- **Facette 3 — Blueprint, Arrangement, Top-Down, MIDI Gate & Patron `compile_song.py` (`build_song.py` LEGACY v2.1)** : [`docs/03_ARCHITECTURE_BLUEPRINT_ET_COHESION.md`](docs/03_ARCHITECTURE_BLUEPRINT_ET_COHESION.md)
- **Facette 4 — DSP Déterministe, Samples, APIs Audio, Remix & Chant (Vocaloid)** : [`docs/04_ANALYSE_SONORE_SAMPLES_APIS_ET_VGM.md`](docs/04_ANALYSE_SONORE_SAMPLES_APIS_ET_VGM.md)
- **Facette 5 — Mixage Multi-Genres, Buses/FX, Quality Gate, Symptôme→Fix & Playbook** : [`docs/05_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md`](docs/05_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md)
- **Facette 6 — v4.0 Pont Strudel headless (frontend de composition ultra-compact, économe en tokens)** : [`docs/06_PONT_STRUDEL_HEADLESS.md`](docs/06_PONT_STRUDEL_HEADLESS.md)

## Briques installées et actives
1. **Gridline `0.7.7`** : MCP natif activé (`mcp.enabled: true`, `mcp.allowEdits: true`, port `47390`), modèles de séparation de stems téléchargés (`htdemucs` et `htdemucs_6s`).
2. **6 serveurs MCP connectés à Antigravity et Zed** :
   - `gridline` (mode live via `--attach` si Gridline GUI est ouvert, sinon édition directe sur `projects/active/projet.gridline`)
   - `audio-index` (`1 898` fichiers indexés dans `samples.db`, vecteurs **LAION CLAP** + analyse **Essentia**, sélection psychoacoustique à 4 couches `select_best_samples` / `evaluate_sample_quality` sur `sample_index.json` + `golden_references/`, et récupération à la volée via **Freesound API v2** & **Lots of Sounds API** `fetch_samples_online`)
   - `audio-analyzer` (`pyloudnorm` LUFS BS.1770, True Peak 4x, spectre 5 bandes, phase, masquage Kick/808)
   - `vocaloid` (syllabation FR/EN, génération `.ustx`, guides vocaux `.wav`, OpenUTAU `0.1.576` dans `tools/OpenUtau/`)
   - `reverse-engineer` (split 6 stems + **Spotify Basic Pitch ONNX** Audio→MIDI + détection d'accords/BPM/tonalité pour flips Rap)
   - `ai-studio-prod` (v4.0 façade unique en production : exactement 3 outils `studio_prepare` / `studio_compose` / `studio_refine`, schémas ~600 tokens, runbook [`docs/AGENT_RUNBOOK_V4.md`](docs/AGENT_RUNBOOK_V4.md))
3. **Plugins Linux natifs installés (729 classes scannées, 0 échec)** :
   - **TDR Nova** (`2.2.3` VST3), **ZL Equalizer 2** (`1.4.2` VST3), **Surge XT** (`1.3.4` CLAP/VST3), **CHOW Tape Model** (`2.11.4` CLAP/VST3/LV2), **Dragonfly Reverb** (`3.2.10` Hall/Room/Plate/ER VST3/LV2), **LSP Plugins** (`1.2.37` CLAP/VST3 : Spectrum Analyzer, Oscilloscope, Crossover, Compressors, Limiter).

## v4.0 — Pont Strudel headless (économie de tokens LLM)
Le LLM peut désormais écrire **un seul `composition.strudel` compact** (~300–600 tokens) au lieu d'un
`arrangement_plan.json` verbeux : `scripts/strudel_eval.mjs` l'évalue en **Node.js headless pur**
(`@strudel/core` + `queryArc`, seed déterministe, 0 Chromium), puis `scripts/strudel_to_score_ir.py`
convertit les Haps en `ScoreIR` + `arrangement_plan.json` **en appliquant les mêmes garde-fous 0 token**
(registre 808 `[31,48]`, apex Lead, voice leading + LIL, b2, clamp 20 ms, `FullCoverageCoreValidator`) ;
le pipeline v3.x (`compile_song.py` → Gridline → quality gate) reste inchangé. Voir
[`docs/06_PONT_STRUDEL_HEADLESS.md`](docs/06_PONT_STRUDEL_HEADLESS.md) et l'exemple
[`assets/composition_trap_example.strudel`](assets/composition_trap_example.strudel).
Dépendances Node : `cd scripts && npm install` (fait automatiquement par `install.sh` si `node` est présent).

**Façade `ai-studio-prod` & solveur de mix mathématique (v4.0, économie maximale de tokens)** : en production, un
seul profil MCP (`ai-studio-prod`, enregistré par `install.sh` dans Antigravity et Zed) expose 3 outils
(`studio_prepare` → retour `CTX|PALETTES|GROOVES` ≤ 450 cars ; `studio_compose(project_dir, strudel_code)` ;
`studio_refine(project_dir, delta_json, strudel_patch)` stateless via `handoff.txt` ≤ 350 tokens). Le prélude
`scripts/strudel_prelude.mjs` (`grid()`, `track()`, `deg()`, `glide()`, `arrange()`) et le lint
`scripts/strudel_lint.mjs` (≤ 2 lignes `ERR L..:..`) escaladent localement à **0 token** avant tout appel LLM ;
le runbook [`docs/AGENT_RUNBOOK_V4.md`](docs/AGENT_RUNBOOK_V4.md) (≤ 750 tokens, préfixe 100 % statique pour le
prompt caching) tient en 2 appels LLM avec budget schemas+runbook+retour < 1 800 tokens
(`tests/test_token_budget_v4.py`). Côté mix, `scripts/math_mix_solver.py` calcule gains/EQ/pan/width/FX sends/
sidechain/limiteur en **< 0,8 s et 0 token** depuis le 1er rendu stems (0 boucle LLM, 0 rendu supplémentaire),
avec détection `AUTO_SWAP_SAMPLE` des sources défectueuses (`tests/test_math_mix_solver.py`).

## Installation automatisée (Fedora / nouveau clone GitHub)
```bash
chmod +x install.sh
./install.sh
```
Le script [`install.sh`](install.sh) détecte automatiquement les paquets système (`dnf`), Gridline, les modèles Demucs, Python `.venv`, PyTorch CPU + Essentia + LAION CLAP, Spotify Basic Pitch ONNX, OpenUTAU, les plugins Linux natifs (`CLAP`/`VST3`/`LV2`), configure Zed/Antigravity/Gridline et indexe vos samples.
Options utiles : `./install.sh --check-only`, `--skip-plugins`, `--skip-clap`, `--no-sudo`.

**Portabilité du clone** : aucun chemin machine n'est codé en dur. La racine se résout via `AI_STUDIO_DIR` (sinon dossier parent de `scripts/`) ; le template projet vide retombe sur `assets/empty_with_mixer_tracks.gridline` si Gridline n'est pas installé (`GRIDLINE_EMPTY_TEMPLATE` pour forcer un autre template). `requirements.txt` est installable depuis PyPI (`essentia>=2.1b6.dev1389`) ; `essentia`/`onnxruntime` sont optionnels au démarrage (dégradation librosa / erreur explicite pour Basic Pitch). **Audition sans rendu .wav** : avec Gridline ouvert (`gridline mcp --attach`), teste chaque piste et le morceau en session via `audio-analyzer:audit_track_soundcheck_fast` (`dsp`/`session`), `balance_tracks_in_session` (volumes équilibrés par solo + `analyze_loudness`, aucun .wav) et `audit_vertical_stage_session`. Le rendu .wav n'est réservé qu'au rendu final (`render_and_audit.py`).

Tests : `.venv/bin/python -m unittest discover -s tests -v` (les tests nécessitant le binaire `gridline` ou des données gitignorées sont automatiquement ignorés sur un clone frais).

## Démarrage rapide
```bash
# Option A (recommandée) : ouvrir Gridline en direct puis lancer Antigravity ou Zed
$AI_STUDIO_DIR/scripts/launch-gridline.sh

# Option B : générer sans ouvrir l'interface, puis ouvrir projects/active/projet.gridline
```
Puis ouvre `$AI_STUDIO_DIR` dans **Antigravity** ou **Zed** et suis les tutoriels dans [`docs/01_SYSTEME_STACK_ET_GUIDE.md`](docs/01_SYSTEME_STACK_ET_GUIDE.md).
