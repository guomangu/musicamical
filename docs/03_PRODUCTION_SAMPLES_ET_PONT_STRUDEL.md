# 03 — Production : samples, APIs, pont Strudel headless & Audio Intelligence v2

> Fusion des anciennes facettes 04 (analyse sonore/samples/APIs/VGM), 06 (pont Strudel headless),
> 07 (guide de conception Strudel pour agents) et 08 (Audio Intelligence v2). Contenu intégral conservé.

---

# Facette 4 — Mémoire Sonore `samples.db`, DSP Déterministe, APIs, Remix & Chant

> **Cheatsheet IA (L'essentiel v2.1 — Loop-First É0)**
> - **Samples fournis initialement** : Si `Samples/` ou un `.gridline` initial existe, lancer **`scripts/index_project_samples.py <proj>`** (MCP `audio-index:index_project_samples`) → `<proj>/samples_catalog.json` (`user_samples_locked = true`, **interdiction d'ajouter de nouveaux samples** externes/APIs ; autorisé de créer des dérivés via `sample_prep_lib.py` dans `samples/`).
> - **Mémoire Sémantique Offline & Palettes (`0 PyTorch`)** : `samples.db` intègre `sample_semantic_cards` (`timbre_card` 1 ligne `<= 140 chars` + `clap_vec` `float16[512]`) projetés contre les 72 ancres pré-encodées `semantic_anchors.npz` (`scripts/build_semantic_anchors.py`). Appeler **`audio-index:propose_palettes`** (`scripts/palette_builder.py`, `< 50 ms`) pour obtenir **3 palettes complémentaires** (Bark24 + relais d'attaques + humeur) au lieu de fouiller 5 000+ fichiers.
> - **Ordre économique** : (1) `index_project_samples.py` (si `Samples/`) → (2) `audio-index:propose_palettes` / `search_samples` / `scripts/select_sample.py` (`< 25 ms`, `timbre_card`, 0 PyTorch) ou synthés natifs `gl.*` (`gl_dsl.synth_voice`) → (3) `audio-index:analyze_sample_dsp` (`< 50 ms`) → (4) `fetch_samples_online(..., monophonic_only=True)` si besoin.
> - **Zéro dissonance chromatique** : Exiger `playable_chromatically == True` (`sample_nature == "mono_oneshot"`) avant de charger un sample mélodique dans `gl.sampler` ; sinon isoler 1 note via `studio_lib.extract_clean_oneshot(...)`.
> - **Hygiène** : `A4 = 440.0 Hz ± 5 ct` (`measure_fft_tuning()` → `recommended_gl_fine_tune_ct`, jamais `pitch_shift` sur `< 80 Hz`) · one-shots `< 2.5 s` en peak norm `-1.0 dBFS` · boucles `>= 2.5 s` stabilisées (`stabilize_stereo_low_mid`) · carte fichiers → [`docs/00_SOMMAIRE.md`](00_SOMMAIRE.md).
> - **Remix / Chant** : `reverse-engineer` (`analyze_song_for_rap_flip` → `split_stems` → `transcribe_audio_to_midi` → `HPF >= 130 Hz`) · `vocaloid` (`list_voicebanks` → `syllabify_lyrics` → `create_ustx` → `synthesize_vocal_preview` → `addAudioClip`).

---

## Chapitre 1 — Arbre de Décision : Sélection & Mémoire Sonore Unifiée (`samples.db`)

* **Classification par tokens** (`indexer._infer_category_and_pack`) : les mots-clés de catégorie (`kick`, `snare`, `open hat`, `808`…) sont comparés aux **tokens** du nom de fichier (séparateurs `-_ .` ignorés, pluriels singularisés), jamais par sous-chaîne : `"chat"` ne matche pas `hat`, `"scalplover"` ne matche pas `cp`. `is_loop` = durée >= 2.2 s ou token `loop/stem/full/drumless/instrumental/acapella` (un one-shot nommé reste one-shot si < 2.5 s).
* **v4.0 — samples Strudel `s("bd")`** : dans le frontend `composition.strudel`, une piste percussive `$kick: s("bd ~ ~ ~")` reste sur **1 rôle atomique = 1 canal/insert** ; `strudel_to_score_ir.py` mappe le nom de sample vers un pitch fixe par rôle (`DRUM_PITCH_BY_NAME` : kick 36, snare 38, clap 39, hat 42…) et le blueprint choisit le plugin (`gl.kick` natif ou `gl.sampler` sur un sample du catalogue avec `root_note` exact).
* **BPM détecté** : nom de fichier (`140 BPM`, `140bpm`) puis, pour les boucles, estimation `librosa.beat.beat_track` (stocké dans `samples_catalog.json` et `samples.db`) ; utilisé par `propose_palettes(bpm=...)` pour exclure les boucles hors tempo.
* **Hauteur détectée** : `midi_root` + `recommended_gl_fine_tune_ct` stockés dans le catalogue et remontés dans `propose_palettes` → `blueprint_tracks[].root_note` / `fine_tune_ct` → appliqués à `gl.sampler` par `song_builder`. La fondamentale passe par YIN + **HPS sur candidats d'octaves + verrou anti-partiel inharmonique** (`_refine_f0_harmonic`) : une cloche dont le partiel 2,76× est plus fort que la fondamentale garde sa vraie racine (sinon classe de hauteur fausse = dissonance). Fenêtre de snap FFT resserrée ±6 %.
* **Détection d'accords** : pics graves (50–900 Hz) mutuellement non-harmoniques (>= 2) OU chroma >= 3 classes fortes quand YIN échoue → `poly_chord_or_dyad`, `playable_chromatically=False` (jamais utilisé en rôle mélodique). Cloche inharmonique à hauteur stable : reste jouable. **Key-fit** : pénalité `key_fit_penalty` si la racine détectée est hors gamme (> 2 st) pour un one-shot mélodique ; le soundcheck rapide remonté aussi `key_fit_ok`. Le schéma blueprint **exige `root_note` pour toute piste `source_type="sample"`**.

Suivre cet ordre exact :

1. **Projet / Samples fournis initialement (`scripts/index_project_samples.py`)** :
   - Exécuter `.venv/bin/python scripts/index_project_samples.py <proj>` (ou MCP `audio-index:index_project_samples`) : scanne **uniquement** `<proj>/Samples/` (et dérivés `samples/`), écrit `<proj>/samples_catalog.json` avec rôles compatibles, `playable_chromatically`, `recommended_gl_fine_tune_ct`, `timbre_card` et recettes `sample_prep_lib.py` (dont les 2 meilleurs `slice_s` d'onsets si `melodic_sequence`).
   - Compléter si besoin par `scripts/audit_project_sources.py <projet.gridline> --prepare-samples <proj>/samples`. Zéro ajout externe (`user_samples_locked = true`).
2. **Mémoire Sémantique Offline (`timbre_card`) & Générateur de Palettes (`propose_palettes`, `< 50 ms`, 0 PyTorch)** :
   - **`semantic_anchors.json` + `.npz`** (`scripts/build_semantic_anchors.py --populate-db`) : 72 descripteurs sur 6 axes (`instrument_family`, `envelope`, `brightness`, `texture`, `mood`, `defect_flags`) pré-encodés une seule fois via CLAP en `float16`. La table **`sample_semantic_cards`** de `samples.db` stocke `clap_vec` (`float16`), `top_tags_json`, `defect_tags_json` et une **`timbre_card`** lisible sur 1 ligne (`<= 140 chars`).
   - **`audio-index:propose_palettes` / `scripts/palette_builder.py`** : combine score rôle, pénalité de chevauchement **Bark24** (surpondérée sur `Kick/Bass` `40–120 Hz` et `Chords/Lead` `800–3500 Hz`), relais d'attaques et adéquation humeur pour proposer **3 palettes distinctes** (`palette_A` Organique/Feutrée, `palette_B` Sombre/Synthétique, `palette_C` Hybride/Percussive). Respecte strictement le verrou `project_samples_dir` en Scénario B.
   - `search_samples`, `select_best_samples` et `scripts/select_sample.py` (`--role`, `--target-key`, `--mono-only`, `--top 8`) retournent `timbre_card` en pur NumPy (`< 25 ms`, 0 PyTorch) avec repli DSP + `"warning": "clap_index_missing_fallback_dsp"` si aucun vecteur CLAP n'est présent.
   - *Maintenance CLI* : `OMP_NUM_THREADS=1 .venv/bin/python scripts/index_samples.py --no-clap --workers 6` (ou `--enrich-clap --batch-size 64` / `--populate-cards`).
3. **Synthèse native `gl.*`** : 13 synthés dans `gl_dsl.SYNTH_REGISTRY` (`search_synth_presets`, zéro téléchargement).
4. **Récupération CC0 en ligne (si absent en local)** : `audio-index:fetch_samples_online(..., monophonic_only=True)` ou `scripts/fetch_samples_api.py --mono-only --no-clap` (clés `.env` : `FREESOUND_API_KEY`, `LOTSOFSOUNDS_API_KEY`).

---

## Chapitre 2 — DSP Déterministe, Pureté `mono_oneshot` & Signature Vocale

* **Outils (`0 GPU`, hors `sfz/`)** : `librosa` (YIN, onsets, HPSS rapide), `praat-parselmouth` (`HNR`, $F_0$ dès `26 Hz`, formants), `scipy`/`essentia`/`soundfile` (phase M/S, Nyquist, harmoniques 808).
* **`sample_nature`** : **`mono_oneshot`** (`playable_chromatically = True`, seul autorisé chromatiquement) · `melodic_sequence` (isoler 1 note via `extract_clean_oneshot`) · `poly_chord_or_dyad` (`DISSONANCE_RISK`) · `inharmonic_bell_or_fx` / `ambient_bed` / `percussion`.
* **`analyze_vocal_signature`** : `est_voix_probable` (`80..400 Hz`, `HNR > 7 dB`), `chant_lent_tenu` (`< 2` attaques/s), `ratio_presence > 0.75`.
* **Scoring 4 couches** : (1) Hard Filter (clipping, silence, phase `< 0.2`, `DISSONANCE_RISK`, `LOW_MID_WIDE_PHASE`), (2) Confort (`r_harsh` `2–5 kHz`, Plomp-Levelt ; assombrir par shelf doux / filtre synthé musical si centroïde `> 2.5 kHz` en `valence < 0` / `lofi_chill` (jamais de LPF brique < 6,5 kHz, voir `config/mix_profiles.json`)), (3) Adéquation rôle & Bark 24 bandes, (4) CLAP optionnel.

---

## Chapitre 3 — Nettoyage, Transitoires & Préparation (`sample_prep_lib.py`)

* **Transitoires rapides ("Tics" / Hi-Hats `>= 1/16`)** : spectre lisse sans pic étroit `Q > 8` (`> 6 dB` dans `2–6 kHz`, sinon creux `gl_dsl.eq_band` `-3..-6 dB`), `Attack >= 1–3 ms`, $T_{\text{decay}} < 0{,}8 \times T_{\text{note}}$, énergie `7–14 kHz` (`HPF >= 500 Hz`), humanisation via `groove_lib.apply_anti_machine_gun`.
* **Normalisation & Extraction** : one-shots `< 2.5 s` via `studio_lib.clean_audio_sample(..., max_peak=0.85)` (`-1.0 dBFS`, fades `2/15 ms`, jamais `loudnorm`) ; boucles `>= 2.5 s` en 24-bit `44.1 kHz` (`loudnorm` doux + `stabilize_stereo_low_mid` si corrélation `< 0.65`) ; extraction 1 note pure via `extract_clean_oneshot(...)`.
* **Pipeline déclaratif centralisé (`scripts/sample_prep_lib.py`)** : utiliser `process_sample_specs(src_dir, out_dir, specs)` (`slice_s`, `eq_peaks`, `varispeed_hz`, `pitch_shift_st`, `saturate_drive`, `time_stretch_rate`, `reverse`, `stabilize_low_mid`, export 24-bit et `manifest.json` avec `measure_fft_tuning()` → `recommended_gl_fine_tune_ct`, où `+X` monte de `+X ct`).
* **Hygiène Basse / 808** : vérifier `f0`, `2*f0`, `3*f0` via `audit_project_sources.py` ; régler `Root note` + `Fine tune` dans `gl.sampler` (**jamais** `pitch_shift` sur `< 80 Hz`).

---

## Chapitre 4 — APIs Audio en Ligne (`Freesound v2` & `Lots of Sounds`)

Clés dans `.env` (`FREESOUND_API_KEY`, `LOTSOFSOUNDS_API_KEY`). Client : `scripts/fetch_samples_api.py` ou `audio-index:fetch_samples_online`.
* **Sécurité des téléchargements** : URLs **HTTPS uniquement** (`_safe_download_bytes`), plafond **25 Mo** par fichier (streaming), refus des réponses JSON sans URL audio. **Jamais de fetch automatique quand `user_samples_locked=True`** (samples fournis : `auto_fetch` est ignoré dans `select_best_from_db`, Invariant #1).
* **Listener multimodal externe** (`listener_backend.py`) : endpoint **HTTPS uniquement**, WAV envoyé en base64 plafonné à **10 Mo**, sinon repli déterministe local.
* **Freesound v2 (`https://freesound.org/apiv2`)** : header `Authorization: Token <KEY>`, requête courte en anglais (`"warm rhodes C4"`, `"acoustic snare dry"`), filtre CC0 automatique, `fields=id,name,duration,tags,previews,license`.
* **Lots of Sounds (`https://api.lotsofsounds.com`)** : 100 % CC0, mots-clés courts (`1–2` mots), conversion obligatoire FFmpeg `-c:a pcm_s24le -ar 44100`.
* **Exemple CLI rapide (DB SQLite < 25 ms & API CC0 sans CLAP)** :
  ```bash
  .venv/bin/python scripts/select_sample.py --role lead_melody --target-key "C minor" --mono-only --top 6
  .venv/bin/python scripts/fetch_samples_api.py --role lead_melody --query "glass bell C4" --mono-only --no-clap --limit 6
  ```
*(Rappel `--query` : filtre lexicographique sur chemin/fichier SQLite ; combinable avec `--role`, `--target-key`, `--mono-only`, `--json`.)*

---

## Chapitre 5 — Protocole Universel Reverse-Engineering & Remix (Audio Source → Tout Genre)

Applicable à **toute** source audio (`.mp3`, `.wav`, `.flac`, `.ogg` : thème de jeu vidéo/VGM, chanson, boucle instrumentale) vers **tout** genre cible (Rap/Trap, Lo-Fi, House, Rock, Orchestral) :

1. **Diagnostic musical** : `reverse-engineer:analyze_song_for_rap_flip(audio_path, target_rap_bpm=<bpm_cible>)` → récupère `detected_bpm`, `key`, `chords_first_bars`, `time_stretch_ratio` et `semitones_if_vinyl_repitch`.
2. **Séparation de stems** : `reverse-engineer:split_stems(audio_path, output_dir=..., model="htdemucs_6s")` (`drums`, `bass`, `other`, `vocals`, `guitar`, `piano` ; utiliser `htdemucs` pour chiptune/8-bit). *Note CPU* : si le fichier dépasse `3 min`, lancer `gridline stems` en commande shell asynchrone pour éviter un timeout MCP.
3. **Transcription MIDI** : `reverse-engineer:transcribe_audio_to_midi(audio_path=stem, bpm=..., quantize_beats=0.25)` (Basic Pitch ONNX `tools/models/nmp.onnx`).
4. **Blueprint & Gate Top-Down** : nettoyer la transcription, figer `harmonic_core` dans `song_blueprint.json` et passer `verify_midi_gate()` (`PASS`). Choisir la stratégie :
   * **A (Sample Flip / Chop)** : découper les stems propres en tranches calées sur la grille (`HPF >= 130–160 Hz` obligatoire pour libérer la nouvelle basse).
   * **B (Remake MIDI)** : rejouer les accords/mélodies transcrits sur les instruments du genre cible (`gl.*` / `gl.sampler`).
   * **C (Hybride)** : garder un stem emblématique filtré (`other`/`piano`/`vocals`) + nouvelle rythmique/basse/contre-chant.
5. **Mixage Remix** : `HPF >= 130–160 Hz` sur les stems conservés, `stabilize_stereo_low_mid` si phase `< 0.65`, EQ miroir sous la nouvelle voix/lead, puis boucle Quality Gate (Facette 5).

---

## Chapitre 6 — Workflow Chant & Synthèse Vocale (`vocaloid` / OpenUTAU)

Lorsqu'un utilisateur demande une piste chantée avec paroles :

1. **Vérifier les banques installées** : `vocaloid:list_voicebanks()` (ex. `TIGER_DS_v106` dans `voicebanks/`).
2. **Syllaber les paroles** : `vocaloid:syllabify_lyrics(text=..., language="fr"|"en")` → compter le nombre exact de syllabes par phrase.
3. **Aligner sur la mélodie validée (`master_sketch.mid`)** :
   * Respecter une tessiture humaine confortable : **Ténor/Baryton `C3–G4` (`MIDI 48–67`)**, **Alto `F3–D5` (`MIDI 53–74`)**, **Soprano `C4–G5` (`MIDI 60–79`)**.
   * **1 syllabe = 1 note** (ou 2 notes liées sur syllabe longue), en conservant des **pauses respiratoires de `0.5–1.5 beat`** toutes les 2–4 mesures.
4. **Générer le projet & la pré-synthèse WAV** :
   * `vocaloid:create_ustx(output_path=".../vocals.ustx", bpm=..., notes=[...])`
   * `vocaloid:synthesize_vocal_preview(ustx_path=".../vocals.ustx", output_wav=".../samples/vocals.wav")` (ou ouvrir dans OpenUTAU via `open_in_openutau` pour rendu DiffSinger final).
5. **Intégrer & Mixer dans Gridline** :
   * Charger `samples/vocals.wav` dans un `gl.sampler` dédié (piste mixeur dédiée → `BUS Music` ou direct `Master 0`), placer sur la playlist via Lua `function apply() project.playlist.addAudioClip(ch, track, 0, 0) end`.
   * **Mix voix & Synchro Arrangement (`P6`)** : `HPF 100–120 Hz`, compression `3:1` (`GR 3–5 dB`), creuser **`1.85 kHz`** (`0 -> -3.5 dB`, `40/120 ms`) automatiquement sur `BUS Music` via `scripts/vocal_arrangement_sync.py` (ou code `SYNC_VOCAL_ARRANGEMENT`) et limiter les contre-chants aux fenêtres respiratoires `>= 0.5 beat` (`mute_counter_during_vocals`).


---

# Partie 2 — Pont Strudel headless v4.0 (ancienne facette 06)

# Facette 6 — Pont Strudel Headless (v4.0) : « Strudel → ScoreIR → AI-Studio → Gridline »

> **Principe v4.0** : Strudel apporte un **frontend de composition ultra-compact** (économie de tokens LLM,
> ~300–600 tokens par morceau au lieu de ~3 000–6 000 en JSON) évalué **en Node.js headless pur**
> (`@strudel/core` + `queryArc`, 0 Chromium, 0 `snd-virmidi`, 0 temps réel). **Toute la couche de
> vérification musicale reste en Python local à 0 token LLM** (`ScoreIR`, voice leading + LIL,
> `midi_autofix`, clamp frontière 20 ms, `FullCoverageCoreValidator`) : on garde 100 % de la sécurité
> harmonique/acoustique de la v3.x avec ~85 % d'économie de tokens.
>
> Détails et inventaire complet des modules v4.0–v4.2 : voir §7 en fin de ce document.
> Index général : [00_SOMMAIRE.md](00_SOMMAIRE.md) (Phase 9). Règles normatives : [../.rules](../.rules) (addendum v4.0).

---

## 1. Architecture en 4 Couches (0 navigateur dans le pipeline IA)

| Couche | Fichier / Outil | Rôle | Coût |
| :--- | :--- | :--- | :--- |
| **1 — Strudel headless** | `scripts/strudel_eval.mjs` (`@strudel/core` + `@strudel/mini` + `@strudel/tonal`) | Évalue `<projet>/composition.strudel` avec `queryArc(0, bars)` + `seed` déterministe, sort un JSON de Haps sur stdout | < 100 ms, 0 token |
| **2 — Compilateur & garde-fous** | `scripts/strudel_to_score_ir.py` | Haps → pistes par rôle → `ScoreIR` + corrections (registre 808, apex Lead, LIL, b2, inertie) + `arrangement_plan.json` | < 150 ms, 0 token |
| **3 — AI-Studio DSP & Mix** | `compile_song.py`, `gl_dsl.py`, `gain_staging_lib.py`, `autofix_transaction.py`, `render_and_audit.py` | Identique v3.x : le plan généré est compilé par le pipeline existant | 0 token |
| **4 — Gridline 0.7.7** | `projet.gridline` headless (CI 7/7) | Rendu déterministe ; pré-écoute Live Strudel **optionnelle** hors boucle IA | — |

Flux : `[prompt]` → Tour 1 `studio_prepare_session` → **Tour 2 : le LLM écrit 1 seul `composition.strudel`**
→ `node scripts/strudel_eval.mjs composition.strudel --bars N --seed S` → `strudel_to_score_ir.py` →
`score_ir.json` + `arrangement_plan.json` → `compile_song.py <projet>` (inchangé) → Tours 3–4 (inchangés).

## 2. Contrat du Fichier `composition.strudel`

Exemple canonique : [`../assets/composition_trap_example.strudel`](../assets/composition_trap_example.strudel).

1. **Métadonnées déclaratives** en commentaire haut de fichier (JSON multi-lignes) :
   `// @strudel-meta {"bpm": 140, "key": "C", "mode": "minor", "time_signature": [4, 4], "seed": 42, "mix_profile": "trap_rap", "sections": [{"name": "intro", "bars": 4}, ...]}`.
2. **1 piste nommée `$role: <expression>` = 1 rôle atomique** (jamais 2 percussions sur le même canal) :
   `$kick: s("bd ~ ~ ~")`, `$snare: s("~ ~ cp ~")`, `$hihat: s("hh*8")`, `$bass: note("<c2 ...>")`,
   `$chords: chord("<Cm7 Abmaj7 Fm7 G7>").slow(2)`, `$lead: note("...").scale("C:minor")`.
3. **Composer en degrés et accords symboliques** (jamais de noms de notes bruts sur les pistes
   basse/mélodie susceptibles de se désynchroniser) : `chord("<...>")` pilote la `harmonic_grid`,
   `.scale("C:minor")` quantifie la mélodie, `arrange([4, intro], [8, verse], ...)` ou `sections` dans
   `@strudel-meta` définit la macro-forme.
4. **Déterminisme** : `--seed S` (ou `seed` du blueprint) est injecté via le contrôle `seed()` avant
   chaque `queryArc` → 2 exécutions identiques produisent le même JSON d'Haps (testé en CI).

## 3. Les 5 Règles d'Or (imposées par le pont et les tests)

1. **Évaluation Node.js headless obligatoire dans le pipeline IA** : `strudel_eval.mjs` sort les Haps
   (début/fin en cycles, `notes` MIDI, `s`, `gain`, `pan`, `cutoff`/`lpf` échantillonné, `chord`)
   en JSON (`schemas/strudel_haps.schema.json`). Chromium/VirMIDI reste un outil de jam humain optionnel.
2. **1 cycle Strudel = 1 mesure** : `start_beat = cycle * beats_per_bar`. Les courbes continues
   (`.lpf(sine.range(1200, 4200).slow(8))`) sont échantillonnées par hap et transmises comme
   `cutoff_hz` par section dans le plan (`role_modifiers`).
3. **1 piste `$` = 1 rôle atomique** : le pont mappe chaque piste sur son propre canal/insert/bus
   via `_default_channels_for_plan` (bus `17..22`, sidechain Kick → 808 préservés).
4. **La couche Python 0 token n'est JAMAIS supprimée** : `ScoreIR`, voice leading + LIL,
   `autofix_score_ir`, clamp frontière 20 ms, `sanitize_note_overlaps`, `FullCoverageCoreValidator`,
   `stem_matrix_evaluator`, `render_and_audit.py` restent actifs.
5. **Double frontend vers le même ScoreIR** : `arrangement_plan.json` (v3.x) et `composition.strudel`
   (v4.0) convergent vers le même `ScoreIR` ; le compilateur v3.x reste la cible tant que le pont
   n'a pas passé `tests/regression_multi_genre.py` 7/7 (PQ >= 7.8).

## 4. Garde-Fous Appliqués par `strudel_to_score_ir.py` (0 token, < 150 ms)

| Bug de l'exemple v4.0 | Garde-fou du pont | Constante |
| :--- | :--- | :--- |
| 808 infrason (c1=24, ab0=20…) | pli par octaves dans le registre basse | `BASS_REGISTER = (31, 48)` |
| Apex Lead perçant (eb6=87) | pli par octaves sous l'apex | `LEAD_APEX_MAX = 74` |
| Fausse relation Bb contre B (G7) | `fix_lead_b2_clashes` + `autofix_score_ir` (snap gamme) | — |
| Accords boueux (LIL) | `optimize_voicings_lil` (miroir de `optimize_voice_leading` + LIL) dans `[48, 72]` | `LOW_INTERVAL_LIMIT` |
| Chevauchement basse mono | dédup des onsets mono + `enforce_frequency_inertia` + `sanitize_note_overlaps` (10 ms) | — |
| Clash de frontière d'accord | clamp frontière 20 ms (`compile_score_ir_to_notes`) | `DEFAULT_GUARD_MS = 20` |
| Clone 8 mesures / arc plat / outro | `validate_full_score_ir` (FullCoverageCoreValidator) | `CLONE_SIMILARITY_MAX = 0.94` |

## 5. Commandes

```bash
# 1) Evaluer le morceau en headless (deps : npm install dans scripts/, voir scripts/package.json)
node scripts/strudel_eval.mjs <projet>/composition.strudel --bars 32 --seed 42 --out <projet>/composition.strudel.haps.json

# 2) Compiler vers ScoreIR + arrangement_plan.json (ré-évalue si --eval)
python3 scripts/strudel_to_score_ir.py <projet> --eval --bars 32

# 3) Pipeline v3.x inchangé ensuite
python3 scripts/compile_song.py <projet>            # arrangement_plan.json -> projet.gridline
python3 scripts/compile_song.py <projet> --probe    # sonde composite 12 mesures
```

## 6. Tests

| Fichier | Commande | Couvre |
| :--- | :--- | :--- |
| `tests/test_strudel_bridge.py` | `.venv/bin/python -m unittest tests/test_strudel_bridge.py -v` | Garde-fous sur l'exemple fautif (registre, apex, b2, LIL, 1 piste = 1 rôle), plan valide schéma, déterminisme seed, eval Node reel + E2E (skip si `node`/`@strudel` absent) |
| `tests/test_token_budget_v4.py` | `.venv/bin/python -m unittest tests/test_token_budget_v4.py -v` | Façade `ai-studio-prod` = 3 outils (AST), budget schemas+runbook+retour prepare < 1 800 tokens, runbook V4 <= 750 tokens, patch `replace_track`, `handoff.txt` <= 350 tokens, prélude/lint/compose E2E (skip si `node` absent) |
| `tests/test_math_mix_solver.py` | `.venv/bin/python -m unittest tests/test_math_mix_solver.py -v` | Contrat §6.1, conflit spectral sévère, perte L/R+mono, parsimonie ±0,3 dB, source défectueuse -> `saturated_tracks_for_swap`, CPU < 1,0 s (8 stems stéréo x 12 mesures) |
| `tests/test_canonical_examples.py` | `.venv/bin/python -m unittest tests/test_canonical_examples.py -v` | **P1** exemples canoniques (bloc js du runbook V4 + asset) : lint → eval → pont avec **0 correction de garde-fou** (skip si `node` absent) |
| `tests/regression_multi_genre_strudel.py` | `.venv/bin/python -m unittest tests.regression_multi_genre_strudel.py -v` | **P6** parité CI **7/7** : fixtures `tests/fixtures/strudel_<profil>.strudel` (incl. `jazz_acoustic` 3/4 et `classical_ambient` 3/4 sans batterie) → lint → eval → pont → solveur sur stems synthétiques (0 erreur, mutation ≤ 25 %, 0 diagnostic `ERR_OVER_REPAIRED`, full_coverage OK, plan valide schéma, basse `[31,48]`, sections = `form=`, déterminisme seed) ; rendu + **PQ ≥ 7.8** seulement si binaire `gridline` présent |
| `tests/test_calibration_ownership.py` | `.venv/bin/python -m unittest tests/test_calibration_ownership.py -v` | **P3** calibration `gl.*` + propriété mixer (`SKIPPED_SOLVER_OWNERSHIP`) + `scalar_master_fix` |
| `tests/test_p4_corridors_pq.py` | `.venv/bin/python -m unittest tests/test_p4_corridors_pq.py -v` | **P4** corridors externes `external_pro_reference` + proxy PQ < 10 ms + `MUTATION_RATIO_MAX` |
| `tests/test_session_bugs.py` | `.venv/bin/python -m unittest tests/test_session_bugs.py -v` | **RETEX** régression des 3 crashs de session + propagation `seed` `@meta` |

## 7. Installation des Dépendances Node

```bash
cd scripts && npm install --no-audit --no-fund    # @strudel/core, @strudel/mini, @strudel/tonal, @tonaljs/tonal (scripts/package.json)
```

`install.sh` tente cette installation automatiquement si `node` est présent (désactivable via `--skip-strudel`).
`scripts/strudel_loader.mjs` mappe `@kabelsalat/web` vers son bundle ESM pour rendre `@strudel/core`
importable en Node pur (son `main` CJS casse l'import ESM). `node_modules/` est gitignoré.

## 8. Façade MCP `ai-studio-prod` & économie maximale de tokens (Spec B)

En production v4.0, un **seul** serveur MCP est exposé à l'agent : `mcp-servers/ai-studio-prod/prod_server.py`
(enregistré par `install.sh` dans Antigravity et Zed), **exactement 3 outils**, schémas ~600 tokens au total :

| Outil | Rôle | Retour |
| :--- | :--- | :--- |
| `studio_prepare(project_dir, intent)` | Indexe les samples, propose palettes + grooves | tabulaire `CTX\|PALETTES\|GROOVES_AVAIL` <= 450 cars |
| `studio_compose(project_dir, strudel_code, palette_id="auto", auto_master=True)` | Écrit `composition.strudel`, évalue Node headless, compile `ScoreIR` + garde-fous 0 token, chaîne le master | résumé <= 400 cars + `handoff.txt` |
| `studio_refine(project_dir, delta_json, strudel_patch, export_mp3)` | Retouche stateless ; patch ciblé `{"replace_track": "<role>", "code": "..."}` | résumé <= 400 cars |

- **Écriture atomique + chaînage auto** : `studio_compose` enchaîne écriture → eval → compile → master
  (quand `gridline` est présent) en 1 seul appel LLM.
- **Escalade locale 0 token** (avant tout nouvel appel LLM) : `scripts/strudel_lint.mjs` renvoie <= 2 lignes
  `ERR L<ligne>:<col> <msg>. Fix: <hint>.` (exit 4 — jamais de stacktrace transmis au LLM) ; le prélude
  `scripts/strudel_prelude.mjs` installe `grid()` (1 symbole d'accord = 1 mesure + progression `__strudel_grid`),
  `track(role, pat)`, `deg(degrés, octave)` (degrés → noms de notes via key/mode), `glide(ms)` (legato),
  `arrange()` ; 4 paliers d'escalade incl. `AUTO_SWAP_SAMPLE` quand le solveur de mix détecte une source défectueuse.
- **Stateless via `handoff.txt`** (<= 350 tokens) : `studio_refine` relit le contexte sans recharger le projet.
- **Prompt caching** : préfixe 100 % statique (system + 3 schémas d'outils + runbook) — runbook dédié
  [`DOCUMENTATION_IA/README.md`](../DOCUMENTATION_IA/README.md) (<= 750 tokens, exemple canonique, flux en 1–2 appels).
- **Budget vérifié en CI** : `tests/test_token_budget_v4.py` exige schemas + runbook + retour `studio_prepare`
  < **1 800 tokens** combinés.

## 9. Solveur de mix mathématique conjoint — `scripts/math_mix_solver.py` (0 token, < 0,8 s)

Après le **1er rendu stems**, plus aucune boucle d'auto-fix LLM : `solve_mix_parameters(stems_audio, tracks_meta,
mix_profile, emotion_vector, bpm, corridors_path, user_overrides)` résout en une passe les **gains, EQ, pan,
width, sends FX, sidechain et limiteur master** optimaux :

- DSP surrogate déterministe NumPy/SciPy : PSD sur K = 512 fréquences, projection 24 bandes de Bark (pondérée
  df), biquads RBJ analytiques vectorisés, corrélation L/R, modèle de perte mono `10·log10(1 + w²(1-c)/(1+c))`.
- Coût = corridor spectral (dead-zone) + masquage Bark (SMR) + bornes de hiérarchie + stéréo/mono + dynamique +
  parsimonie ; seuils SSOT depuis `config/mix_profiles.json` (via `mix_config.py`) et
  `golden_references/spectral_corridors.json`.
- 4 blocs couplés x 2 passes : (1) EQ L-BFGS-B, (2) gains `lsq_linear`, (3) pan/width/DRR en forme close
  (`least_squares`), (4) temporel `sosfilt` + sidechain + bissection du limiteur (<= 8 itérations).
- Sortie = dict §6.1 du spec + `saturated_tracks_for_swap` → escalade `AUTO_SWAP_SAMPLE` (0 token).
- Tests bloquants : `tests/test_math_mix_solver.py` (6 tests : contrat §6.1, conflit spectral sévère, perte
  L/R + mono, parsimonie ±0,3 dB, source défectueuse, CPU < 1,0 s sur 8 stems stéréo x 12 mesures).

## 10. Scénarios C (Remix) et D (Chant) dans la façade v4.1

- `studio_prepare(project_dir, intent, remix="<chemin>", lyrics={...})` : comme en v3.3, `remix`
  renvoie BPM/clé/grille du morceau source (Scénario C) et `lyrics` les syllabes/pauses (Scénario D).
- `studio_compose(..., remix_source_path="...", lyrics_spec={...})` : consigne `scenario: "C"|"D"` +
  la source/les paroles dans `song_blueprint.json` (généré automatiquement, valide schéma).
- Piste vocale dans `composition.strudel` : déclarez simplement `$vocals: ...` (1 piste = 1 rôle) ;
  le pont classe le rôle `vocals` et `song_builder` applique `vocal_arrangement_sync.py` (P6) dès
  que `.ustx`/`vocals.wav` est présent. Stems découpés d'un remix : pistes `$stem_<nom>` (rôle `remix_stem`).


---

# Partie 3 — Guide de conception Strudel (agents autonomes, ancienne facette 07)

# 07 — Guide de Conception Strudel (anti-erreurs capitalisées)

Capitalise les **erreurs réelles** rencontrées en production (session `rap-serene-rythme`, C mineur)
pour concevoir vite et juste. Outil associé : `scripts/preflight_strudel_gate.py` (simulateur
pre-flight du MIDI Gate, **0 rendu, 0 LLM, ~2 s**) — à lancer **avant** tout rendu coûteux.

## 1. Dictionnaire degrés Strudel ↔ demi-tons (C mineur, octave 4)

`deg("<...>", oct)` utilise `SCALES.minor = [0,2,3,5,7,8,10]` et
`midi = tonic + 12*(deg//7) + SCALES[deg%7]` avec `tonic = 60`.
Reproduit en CLI : `python3 scripts/preflight_strudel_gate.py --degrees C:minor`.

| degré | pc | nom | rel. (st) | MIDI |
|------:|---:|-----|----------:|-----:|
| 0 | 0 | C | **+0** | 60 |
| 1 | 2 | D | **+2** | 62 |
| 2 | 3 | D# | **+3** | 63 |
| 3 | 5 | F | **+5** | 65 |
| 4 | 7 | G | **+7** | 67 |
| 5 | 8 | G# | **+8** | 68 |
| 6 | 10 | A# | **+10** | 70 |
| 7 | 0 | C | **+12** | 72 |
| 8 | 2 | D | **+14** | 74 |

**Piège n°1** : le degré 4 n'est PAS « +4 st », c'est **+7 st** (G en mineur). Concevoir les
motifs depuis cette table, jamais depuis l'intuition « degré = intervalle en tons ».

## 2. Sémantique de `slow(N)` dans le pont headless

`queryArc(0, bars)` retourne des événements en **cycles** (1 cycle = 1 mesure = 4 beats).
`.slow(N)` divise le nombre de degrés par mesure : `degrés/mesure = L/N`, `step = 4/(L/N)` beats.
Reproduit en CLI : `preflight_strudel_gate.py` (fonction `slow_step`).

| seq de L degrés | modificateur | degrés/mesure | step (beats) |
|---|---|---:|---:|
| 8 | aucun | 8 | 0.5 (croches) |
| 8 | `.slow(8)` | 1 | 4.0 (1/mesure) |
| 128 | `.slow(8)` | 16 | 0.25 (doubles-croches) |
| 256 | aucun | 256 | 0.015625 (1/64) — **dangereux** |

**Règle** : 16/mesure → seq de 128 avec `.slow(8)` ; 8/mesure → seq de 64 avec `.slow(8)` ou seq de 8 sans slow.

## 3. Le piège destructeur : `fix_lead_b2_clashes`

Dans `scripts/strudel_to_score_ir.py` (fonction `fix_lead_b2_clashes`), **après** `autofix_score_ir`,
le pont réécrit toute note du lead/counter située à **1 demi-ton d'une pc de l'accord actif** vers
la pc de gamme la plus proche. Sur la session fautive : **459–549 notes réécrites** (D→Eb, G→Ab…).

**Conséquence** : un motif leap+fill conçu à la main est cassé après fix → `gap_fill` s'effondre
(1.0 → 0.2–0.4). **Stratégie** : concevoir avec des degrés b2-safe (§4), et lancer le pre-flight
**avant** toute tentative de rendu pour voir le `gap_fill` réel post-fix (`b2_fixed_by_bridge` dans
le rapport dit combien de notes ont été réécrites).

## 4. Table des degrés b2-safe par accord (progression i-VI-III-VII en C mineur)

Un degré est « b2-unsafe » sur un accord si sa pc est à 1 st d'une pc de l'accord (mêmes qualités
d'accords que le pont, via `parse_chord_symbol`).
Reproduit en CLI : `preflight_strudel_gate.py --safe-degrees "Cm7 Abmaj7 Ebmaj7 Bb7" --mode minor`.

| Accord | pcs | degrés interdits (clash b2) | degrés sûrs |
|---|---|---|---|
| Cm7 | {0, 3, 7, 10} | 1 (D), 5 (Ab) | **0, 2, 3, 4, 6, 7** |
| Abmaj7 | {0, 3, 7, 8} | 1 (D), 4 (G), 5 (Ab) | **0, 2, 3, 6, 7** |
| Ebmaj7 | {2, 3, 7, 10} | 1 (D), 2 (Eb), 5 (Ab) | **0, 3, 4, 6, 7** |
| Bb7 | {2, 5, 8, 10} | 2 (Eb), 4 (G) | **0, 1, 3, 5, 6, 7** |

**Intersection (sûr partout)** : degrés **[0, 3, 6, 7]** = C, F, Bb, C' (deg 7 = octave de 0).
Conclusion : sur i-VI-III-VII, un lead 100 % b2-safe ne peut pas avoir `gap_fill = 1.0` sur les 4
accords simultanément. Solutions : (1) changer la grille (ex. Cm7 seul → sûrs = {0,2,3,4,6,7}),
(2) accepter quelques b2 et vérifier au gate final, (3) motif adapté par accord (§6).

## 5. Règle du clamp d'octave (LEAD_APEX_MAX = 74)

`apply_register_guards` fait `while p > 74: p -= 12` : degré max sans clamp = **8** (D' = 74),
au-delà −12 st. **Règle** : `base_max + motif_max ≤ 8`. Un clamp de −12 st crée un leap non
rempli → `gap_fill` s'effondre. Le pre-flight rapporte `lead_apex_clamped` (guards).

## 6. Stratégie : motif par section, frontières propres

Le gate mesure la **topline complète concaténée** (toutes sections à la suite) : les frontières
inter-sections comptent dans `gap_fill`. Règles pour `gap_fill = 1.0` :

1. Chaque motif commence et finit sur le **même degré** → diff de frontière = 0.
2. Tout leap (|d| ≥ 5 st) doit être suivi d'un mouvement **contraire de 1 à 3 st**
   (`(d * next < 0) and (1 <= abs(next) <= 3)`).
3. Pattern éprouvé (24 pas, `.slow(4)`, notes tenues `_` + silences `~`) : `deg("0 _ _ ~ 3 _ 2 ~ 0 _ ~ ~ 2 _ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4)` →
   notes chantantes `0.8–1.5 notes/s`, `rest ~0.35–0.55`, `lead_not_frantic = PASS`.
   C'est le lead de l'exemple canonique de [`DOCUMENTATION_IA/README.md`](../DOCUMENTATION_IA/README.md) (éviter `.slow(2)` en croches continues).
4. `rest_ratio` ∈ [0.20, 0.50] : ~2 `~` sur 16 éléments avec durées < step → ~0.23–0.42.
   Ne pas mettre de `.legato()` (allonge les notes, `rest_ratio` peut tomber sous 0.20).

## 7. Checklist pre-flight avant tout rendu final

```bash
# 1. Pont (génère haps + score_ir + arrangement_plan + rapport preflight intégré)
.venv/bin/python scripts/strudel_to_score_ir.py projects/<nom> --eval
# 2. PRE-FLIGHT DU GATE (0 rendu, ~2 s) — l'étape qui aurait évité ~10 itérations
.venv/bin/python scripts/preflight_strudel_gate.py projects/<nom>
# Doit sortir "passed": true : gap_fill >= 0.60, stepwise >= 0.65, rest in [0.20, 0.50]
# 3. Seulement ensuite : le rendu final coûteux
```

Si le pre-flight échoue sur `gap_fill` : inspecter `bad_leaps` (avec `start_beat`) et corriger
le motif dans `composition.strudel` — jamais `arrangement_plan.json` à la main (régénéré).

## 8. Pièges déjà rencontrés (ne pas refaire)

| # | Piège | Symptôme | Fix |
|---|---|---|---|
| 1 | `V < -0.2` avec octave-fold brutal | split de registre qui plie une note isolée → leaps ±12 | Résolu en v4.3+ : transposition par phrase entière préservant `gap_fill` (valences sombres libres) |
| 2 | `sometimesBy(x>0, degradeBy)` sur lead/counter | clashes b2 bloquants au gate final | `sometimesBy(0.0, …)` ou supprimer sur le lead mélodique |
| 3 | `.slow(32).fast(2)` ou `.legato(1.2)` | notes trop denses/allongées → `rest_ratio` < 0.20 | durées < step, ~2 rests/16 |
| 4 | seq longue sans `.slow()` | notes de 1/64 de mesure | `.slow(8)` avec seq = 8×bars voulues |
| 5 | base montante degrés 0→4 | deg 11 = 79 → clamp −12 → gap_fill 0.2–0.4 | base max 1, motif max deg 7 |
| 6 | modifier `arrangement_plan.json` à la main | écrasé au prochain `--eval` | toujours éditer `composition.strudel` |
| 7 | motif conçu sans penser à `fix_lead_b2_clashes` | ~500 notes réécrites, gap_fill détruit | pre-flight + degrés b2-safe (§4) |
| 8 | `.slow(2)` sur `sectionGrid(...)` | accords tenus 8 temps alors que `harmonic_grid` avance toutes les 4 temps (clash harmonique) | ne jamais mettre `.slow(2)` sur `sectionGrid` ; répéter le symbole (`"Gm Gm Ebmaj7 Gm"`) |
| 9 | `$arp` classé `lead` + cloche inharmonique | `Arp Z1/Z2` à `+6 dB` sur les accords avec partiels faux (`-237 ct`) | `"arp"` → `"counter_melody"` (`-9 dB`) et `inharmonic_bell_or_fx` interdit sur `arp`/`counter` |
| 10 | `ch.preset` sur `gl.analog` (*PWM Strings* / *Velvet Pad*) | Pulse+Saw ouvert `LP 12 @ 3.2 kHz` + Chorus 50 % + Reverb 35 % → timbre électrique artificiel | `song_builder.py` honore `cutoff_hz`/`attack`/`release` + force `LP 24`, `Chorus=0`, `Reverb=0` sous `2.2 kHz` ; privilégier samples organiques (`DBD_HI STR_C_1`, `headroom_piano_c4`) |

## 9. Références rapides

- Gate : `mcp-servers/audio-analyzer/musicality_metrics.py` (seuils : stepwise ≥ 0.65, gap_fill ≥ 0.60, rest ∈ [0.20, 0.50], contrary_or_oblique ≥ 0.55)
- Pont : `scripts/strudel_prelude.mjs` (`deg`, `bassline`, `kit`), `scripts/strudel_to_score_ir.py` (`fix_lead_b2_clashes`, `LEAD_APEX_MAX = 74`)
- Pre-flight : `scripts/preflight_strudel_gate.py` (`--degrees`, `--safe-degrees`, rapport `bad_leaps`)
- Tests : `tests/test_preflight_gate.py`, `tests/test_canonical_examples.py` (exemple canonique à 0 correction ET pre-flight PASS)


---

# Partie 4 — Audio Intelligence v2 (ancienne facette 08)

# 08 — Audio Intelligence v2 : qualité physique, tagging psychoacoustique & agencement cohérent

Intègre le pack de spécification « MIR v2 » dans l'idiome du projet (**Python in-process,
décodage unique**), sans rien casser : le Node reste réservé au pont Strudel headless.

## 1. Pourquoi pas le stack v1 tel quel (audit)

| Composant v1 | Défaut | Équivalent v2 retenu dans le projet |
|---|---|---|
| CLI spawning (sox, aubio, ffmpeg) | fork/exec + triple décodage disque par fichier | **décode once** via libsndfile (`soundfile`) en mémoire, zéro subprocess (`sample_quality_v2.decode_once`) |
| node-wav (readFileSync, 24/32-bit instable) | OOM / rejets silencieux HD | soundfile/librosa lisent WAV 16/24/32-bit float, FLAC, OGG nativement |
| meyda sur main thread | bloque l'event loop, pas de HPSS | DSP Python côté `ProcessPoolExecutor` (déjà utilisé par l'indexeur) |
| essentia.js WASM brut | coût init WASM, fuites VectorFloat | `librosa.decompose.hpss` (HPSS), `librosa.pyin` (F0 10 ms ≈ CREPE), numpy FFT |
| libebur128 (RPM) | dépendance système | `pyloudnorm` (BS.1770) déjà embarqué + **true peak x4** maison (`resample_poly`) |
| onnxruntime-node CLAP/CREPE | modèles lourds à télécharger | CLAP **optionnel** (déjà supporté via `ClapEmbedder`) ; repli déterministe = embedding DSP (Bark24 + métriques) ; CREPE ≈ `librosa.pyin` |
| sqlite-vec (npm natif) | build natif risqué | recherche vectorielle **numpy** sur `semantic_anchors.npz` (déjà en place, < 2 ms) + filtres SQL stricts dans `samples.db` |
| piscina workers | pool Node | `ProcessPoolExecutor` Python (indexeur, workers=4) |

## 2. Quality Gate physique (jalon 1) — `scripts/sample_quality_v2.py`

`quality_gate_v2(x, sr)` (numpy/scipy, cible < 15 ms/fichier) -> `PASS` / `REJECTED` + défauts :

| Défaut | Détection | Seuil |
|---|---|---|
| `CLIPPED` | True Peak par suréchantillonnage x4 (pics inter-échantillons) OU échantillons saturés | TP > +0.5 dBTP ou >= 3 consécutifs à 0.999 |
| `UPSAMPLED_LOSSY` | coupure brique spectrale (bins 500 Hz) sur source large bande | coupure à ~11 kHz ou ~16 kHz (sr >= 44,1 kHz) |
| `NOISY_FLOOR` | SNR spectral (pics tonaux vs médiane du spectre), gardé si fraction tonale >= 2 % | SNR < 30 dB |
| `DC_OFFSET` | moyenne du signal | > 0.005 |

Ingestion : `decode_once` (libsndfile) -> `remove_dc_offset` -> `smart_trim` (seuil -65 dBFS,
pré-roll 2 ms pour préserver l'attaque). Les samples `REJECTED` sont exclus de la sélection
(`select_sample.py` joint `sample_quality_v2`) et marqués `hard_reject` côté DSP.

## 3. Profilage morphologique (jalon 2) — `profile_morphology`

- Contour `F0(t)` trame 10 ms (`librosa.pyin`) + pic spectral des 80 premières ms pour le glide.
- **Signature 808** : `F0_tail` (médiane des 30 % voiced finaux) dans [28, 68] Hz ET
  `ΔF0 = F0_head - F0_tail > 35 Hz` -> tag `808` (+ `sub`).
- **T30** (enveloppe RMS lissée, pic -> -30 dB) : `< 0.30 s` percussive_short, `< 1.10 s` medium, sinon `long_sustain`.
- **Saturation** : crest factor temporel `CF = 20 log10(max|x|/rms(x))` + ratio d'énergie harmonique
  HPSS dans [2.5, 8] x F0 : `CF < 6.5 dB` + (rolloff85 > 350 Hz ou ratio > 0.15) + `F0_tail < 65 Hz`
  -> `timbre:saturated_distorted` ; `CF >= 9.5 dB` + rolloff < 160 Hz -> `timbre:clean_sub`.
- Sortie : rôle (`808`/`bass`/`kick`/`snare`/`perc`/`hihat`/`synth`), `pitch_midi`, `pitch_confidence`,
  `decay_t30_ms`, `crest_factor_db`, rolloff/centroid, **bark_24** (24 bandes normalisées), tags.

Jalon 2 vérifié en CI (`tests/test_sample_quality_v2.py` + `--selftest`) : 808 synthétique
(120 -> 45 Hz en 50 ms, T30 ~1.6 s, tanh CF ~5 dB) -> tags `[808, long_sustain, saturated_distorted]`,
`pitch_midi = 29 ± 1`.

## 4. Agencement cohérent (jalons 3 & 4)

- **Anti-masquage** (`spectral_collision`, `suggest_hpf_hz`) : `C(A,B) = Σ w_b E_A(b) E_B(b)` sur les
  24 bandes Bark, poids x4 sous 500 Hz ; HPF suggéré à la fréquence de croisement. La sélection
  applique déjà `compute_masking_penalty(bark_cand, bark_mix)` quand un mix de référence est fourni.
- **Phase Kick/808** (`phase_alignment`) : corrélation croisée bande [30, 100] Hz sur les 50
  premières ms -> `none` / `invert_polarity` / `delay_ms < 8 ms`, avec garantie
  `rms_bande(somme) >= max(rms_kick, rms_bass)` (pas d'annulation). Câblé dans
  `audit_project_sources.py` (`kick_bass_phase` + warning `PHASE_KICK_BASS`) ; les autofixes
  `INVERT_808_POLARITY` / `SHIFT_BASS_MICRO_MS` restent les bras armés côté rendu.
- **Sélection hybride SQL + vecteur** : filtres durs SQL (`quality_v2.status != 'REJECTED'`,
  `hard_reject = 0`, bornes de rôle) + proximité vectorielle (CLAP si indexé, sinon npz DSP),
  pénalité de masquage Bark24 vs mix actif.

## 5. Câblage indexeur

`sample_quality_v2` (table SQLite, cascade `ON DELETE CASCADE`) peuplée à l'indexation par les
workers (`populate_dsp_metrics`) ; rapport : `q2_pass` / `q2_rejected` ; purge des fichiers
supprimés incluse. CLI : `python3 scripts/sample_quality_v2.py <fichier.wav>` et `--selftest`.

## 6. Tests

`tests/test_sample_quality_v2.py` : jalons 1-4 synthétiques (4 fixtures qualité, 808, collision,
phase) + câblage indexeur/sélection (REJECTED exclu). Budget gate < 50 ms/fichier en CI
(cible spec : 15 ms).

---

## 7. Synthèse Architecture v4.0–v4.2 : Politique 100 % Vrais Samples (`samples/`) & Inventaire Modules

### 7.1 Décisions d'architecture Strudel headless (v4.0)
| Choix | Décision retenue |
| :--- | :--- |
| Évaluation Strudel | Node.js **headless pur** (`scripts/strudel_eval.mjs`, `@strudel/core` `queryArc`, `seed` déterministe, < 100 ms) — 0 Chromium, 0 VirMIDI, 0 temps réel |
| Couche musicale | `ScoreIR` + garde-fous **100 % locaux (0 token)** : registre 808 `[31,48]`, apex Lead `<= 74`, voice leading Viterbi + LIL, `autofix_score_ir` + `fix_lead_b2_clashes`, clamp 20 ms, `sanitize_note_overlaps`, `FullCoverageCoreValidator`, boucle ascendante `bottom_up_analyzer.py` |
| Double frontend | `composition.strudel` (v4.x) et `arrangement_plan.json` (v3.x) → **même `ScoreIR`** via `scripts/strudel_to_score_ir.py`, puis `compile_song.py` |
| Façade MCP production | `ai-studio-prod` (`mcp-servers/ai-studio-prod/prod_server.py`) : exactement 3 outils `studio_prepare` / `studio_compose` / `studio_refine` (~600 tokens de schémas), retour prepare ≤ 450 cars, `handoff.txt` ≤ 350 tokens, runbook [`DOCUMENTATION_IA/README.md`](../DOCUMENTATION_IA/README.md) ≤ 750 tokens, budget total < 1 800 tokens (`tests/test_token_budget_v4.py`) |
| Mix 0 token | `scripts/math_mix_solver.py` : `solve_mix_parameters(...)` (< 0,8 s, 0 token, 0 rendu, PSD 512 + Bark 24 + RBJ analytique, `AUTO_SWAP_SAMPLE`) |

### 7.2 Priorité absolue aux vrais samples enregistrés (`samples/`, v4.2)
Les synthés natifs `gl.*` restent un **repli de sécurité uniquement** lorsqu'aucun sample réel sain n'existe ; toute piste tonale ou percussive privilégie les vrais samples de `$AI_STUDIO_DIR/samples/` :
* **Analyse pitch & pureté robuste (`scripts/audio_metrics.py` + `scripts/bottom_up_analyzer.py`)** : stabilité de hauteur combinant **MAD** et **P85 replié à l'octave** + fraction de trames hors pitch (`frac_off > 14 %` au-delà de `0.75 st`) pour rejeter 100 % des découpes multi-notes/loop-chops (qui trompaient MAD seul, ex. `guitar_pluck_b3_440.wav` contenant B3 puis E2/D3 à 280 ms) ; garde directe sur la forme d'onde (`_audit_tonal_sample_waveform`) exécutée dans `prod_server._pick_unique_from_pool` et `bottom_up_analyzer.py` Niveau 1 ; verrou `_refine_f0_harmonic` protégé (conserve la fondamentale dominante `>= 65 %` tout en détectant les partiels inharmoniques aigus de cloches `> 700 Hz, ratio >= 1.5`).
* **Gate d'agréabilité sans faux positifs tonaux (`scripts/timbre_pleasantness.py`, `scripts/select_sample.py`)** : `peak_harsh_db` n'est évalué que si `r_harsh >= 0.06` (énergie audible réelle dans 2–5 kHz — un piano/Rhodes/guitare chaud avec `r_harsh = 0.000` n'est plus rejeté `SWAP` pour un harmonique discret au-dessus du plancher numérique) ; `CREST_MAX_DB_TONAL` recalibré pour les vrais pianos/plucks (`19.5 dB` accords/lead, `14.5 dB` pad).
* **Quality gate v2 percussif (`scripts/sample_quality_v2.py`)** : `NOISY_FLOOR` exige aussi une plage dynamique temporelle `< 15 dB` afin de ne plus rejeter les vrais hi-hats/snares/claps à spectre large et décroissance rapide.
* **Centrage d'octave polyphase universel (`scripts/sample_fit_guard.py`)** : `prepare_octave_shifted_sample(src, proj, root, shift_octaves=±1)` génère sans artefacts dans `<projet>/derived_samples/` les dérivés `±12 st` pour tout sample tonal (`808`, `piano`, `synth`, `pad`, `guitar`, `vocal`) ; `max_semitone_shift_st_by_role` (`chord_keys: 10, pad_atmosphere: 10, lead_melody: 9, bass_sub: 9`) ; exclusion stricte des stabs métalliques sur `chord_keys`/`pad_atmosphere`, des cris vocaux (`_VOCAL_NAME_RE`) sur percussions, et des reverse/swells sur `lead`/`arp` ; les `mono_oneshot` soutenus ne sont jamais filtrés comme des boucles BPM.
* **Instruments studio extraits de `samples/sfz/`** : `samples/piano/rhodes_mk1_d4.wav` (Fender Rhodes Mk1, `root=62`), `samples/piano/headroom_piano_c4.wav` (Intimate Acoustic Piano, `root=60`), `samples/piano/rhodes_tine_lead_f4.wav` (Rhodes tine haute vélocité, `root=65`) et `samples/piano/headroom_piano_lead_fs4.wav` (Piano studio close, `root=66`) dérivés directement des banques SFZ déjà présentes dans `samples/sfz/` (fondu 3 ms anti-clic) ; `index_project_samples.py` exclut `sfz/` comme `indexer.py`. Diversité déterministe pilotée par le `seed` du morceau parmi les 3 meilleurs candidats sains du même rang dans `prod_server._resolve_palette`.
* **Enveloppes & polyphonie musicales `gl.sampler` (`scripts/song_builder.py`)** : `pad` (`voices=8, attack=0.08s, release=0.45s, LPF=6 kHz`), `chords` (`voices=8, attack=0.008s, release=0.28s, LPF=6.5 kHz`), `lead/arp` (`voices=6, attack=0.005s, release=0.22s, LPF=9 kHz`), `bass/808` (`voices=1` mono, `attack=0.003s, release=0.10s`), batterie (`one_shot=1, voices=4`) ; réinitialisation `spec.preset = ""` et filtrage des paramètres synthé (`vibrato_delay_ms`, etc.) sur `gl.sampler`.

### 7.3 Inventaire synthétique des modules clés (v3.0–v4.2)
* `scripts/blueprint_safety.py` : audit projet-wide (`root_note`, `MAX_SEMITONE_SHIFT`, boucles CQT + BPM `[0.88;1.14]`, confinement cloches `{1,5,8}`).
* `scripts/sample_fit_guard.py` : politique F4, `register_fit`, dérivation polyphase `±12 st`, garde finale `enforce_blueprint_sample_fit`.
* `scripts/bottom_up_analyzer.py` : boucle hiérarchique ascendante 4 niveaux (`1_sounds -> 2_chords -> 3_tracks -> 4_song`).
* `scripts/mix_config.py` + `config/mix_profiles.json` : source unique des constantes de mixage.
* `scripts/stem_matrix_evaluator.py`, `scripts/compile_song.py`, `scripts/gain_staging_lib.py`, `scripts/sample_prep_lib.py`, `scripts/groove_lib.py`, `scripts/transitions_lib.py`, `scripts/midi_autofix.py`, `mcp-servers/audio-analyzer/musicality_metrics.py`.

---

## 8. Pipeline psychoacoustique unifié : `samples.db` + sélection relationnelle + rendu natif (v5.0)

Remplace la logique éclatée d'indexation/sélection (`indexer.py`, `preset_indexer.py`, `select_sample.py`,
`sample_fit_guard.py`, `sample_quality_v2.py`, `sample_prep_lib.py` — tous **dépréciés**, conservés pour
compatibilité) par un pipeline unique inspiré de `MTG/essentia`, `AudioCommons/timbral_models`,
`facebookresearch/demucs`, `DBraun/DawDreamer`, `spotify/pedalboard` et `csl-pku/pyloudnorm` :

| Composant | Rôle |
| :--- | :--- |
| `mcp-servers/audio-index/unified_indexer.py` | **`samples.db` (racine) = SSOT**, table `sound_assets` : métadonnées techniques (f0, attaque log, LUFS = -18, crest, centroïde, 4 bandes d'énergie) + **7 scores perceptuels [0,100]** (warmth, brightness, roughness, hardness, depth, boomy, sharpness). CLI : `index` / `migrate-catalog` / `query`. `samples_catalog.json` est déprécié (clé `_deprecated`), migré via `migrate-catalog`. |
| `scripts/audio_prep_pb.py` | Prépa déterministe à l'ingestion : trim sous -60 dBFS + fondu 2 ms, normalisation **-18.0 LUFS** (`pyloudnorm`, gain via `pedalboard` si dispo). |
| `scripts/palette_selector.py` | **Sélection relationnelle en 4 étapes** (ancrage rythmique → basse complémentaire du kick → couche harmonique → lead contrasté, tessiture `[min_safe_midi, max_safe_midi]` + Low Interval Limits) + score `S_palette = w1·(1-D_corridor) + w2·(1-M_spectral) + w3·P_timbre` (Bark24). |
| `mcp-servers/ai-studio-prod/synth_render.py` + `scripts/index_vst_presets.py` | **Rendu natif à la hauteur exacte** des pistes tonales (DawDreamer + VST3 Surge XT/Vital/Dexed si installé, sinon synthèse additive builtin) ; politique stricte : WAV réservé aux rôles percussifs, ±2 st max (accord kick/tom), **aucun pitch-shift de one-shot** (`derived_samples/multizone/` supprimé du flux). Les presets SFZ/VST3 sont indexés via une note de référence (C2 basses / C4 médiums, 2 s, vélocité 100) passée dans le même analyseur. |
| `golden_references/spectral_corridors.json` (schema 3.2) | Corridors spectraux **par stem** (`drums`/`bass`/`other`/`vocals` : bark24 p10/p50/p90 + centroïde + crest + plages `ac_*`) — analytiques hors-ligne, ou mesurés via `bootstrap_golden_refs.build_stem_corridors_from_masters()` (Demucs `htdemucs`) quand des masters externes existent. |

Tests : `tests/test_sqlite_catalog.py`, `tests/test_unified_indexer.py`, `tests/test_relational_palette.py`,
`tests/test_synth_render.py`, `tests/test_stem_corridors.py`.
