# 04 — Mixage multi-genres, quality gate & apprentissage

> Ancienne facette 05 (contenu intégral conservé ci-dessous).

---

# Facette 5 — Mixage Multi-Genres, Stems, Auto-Fix, Quality Gate & Références

> **Cheatsheet IA (L'essentiel v2.1 — Loop-First É1 → É4)**
> - **Validation Incrémentale Loop-First (`scripts/loop_first_runner.py` → `pipeline_state.json`)** :
>   - **É1 Sound Check Unitaire (`audio-analyzer:audit_track_soundcheck` / `scripts/soundcheck_lib.py`)** : teste chaque piste isolée jouée sur `2–4` mes. (audibilité, justesse `<= 8 ct` (soundcheck ; fondation 6 ct / peak expressif 12 ct / warning 6,5 ct — `config/mix_profiles.json`), intégrité chromatique, propreté `2–5 kHz`, enveloppe vs BPM, fidélité valence) via `ListenerBackend` (`DeterministicSemanticListener` 0 GPU ou `MultimodalAudioListener`). Optionnel : `scripts/export_palette_preview.py --human-gate` (`renders/palette_preview.wav`).
>   - **É2 Tests Verticaux sur Noyau `4/8` mes. (`--core-only` + `audio-analyzer:audit_vertical_step` / `scripts/vertical_tester.py`)** : `V1_LOW_END` → `V2_GROOVE` *(sauté si `has_drums=false`)* → `V3_HARMONY` *(conflit chroma CQT basse/accords + `low_mids`)* → `V4_LEAD` → gèle `core_loop_locked = true`. **Isolation stricte** : interdit d'éditer `forbidden_tracks` ($1 \dots k-1$) ; max 2 retries avant `SWAP_SAMPLE`.
>   - **É3 Audit des Coutures (`audio-analyzer:audit_horizontal_seam` / `scripts/horizontal_tester.py`)** : rend uniquement les fenêtres `4` mes. (`±2` mes.) sur chaque transition majeure + `Outro` (`ΔLUFS`, `beat4_dropout`/`filter_sweep`, `0` glitch `±150 ms`, Outro `< -45 dBFS`).
> - **É4 Boucle fermée & Auto-Fix** : Lancer `render_and_audit.py <projet.gridline> --profile <genre> --blueprint song_blueprint.json --range <b1>-<b2> --no-neural --auto-fix --max-passes 2` puis **1 seul rendu complet** `--mp3 --sections "..."` (`renders/autofix_history.json`).
> - **Stems & Référence Spectrale** : Auditer les stems via `render_project_stems` + `analyze_stem_hierarchy` (bornes `lead_vs_harmony` `[3.0, 9.5] dB`, `kick_bass_transient_overlap`, `pink_slope_db_per_oct` `-3..-6.5`). `render_and_audit` calcule aussi `spectral_match_score` + `corridor_score` (`0..1`) via `golden_references/spectral_corridors.json` versionné (0 PyTorch).
> - **Genres sans batterie (`has_drums=false` / `classical_ambient`)** : Passer `--no-drums` ou `--blueprint song_blueprint.json` (`"has_drums": false`) ; le gate désactive l'exigence `dynamic_arc` et filtre les résonances inter-harmoniques.
> - **Topologie Buses (`setup_standard_buses`)** : `BUS Drums (17)` · `BUS Bass (18)` · `BUS Music (19)` · `FX Room (20)` · `FX Delay (21)` · `FX Hall (22)` · `Master (0)` configurés via **`scripts/gl_dsl.py`** (carte → [`docs/00_SOMMAIRE.md`](00_SOMMAIRE.md)).

---

## Chapitre 1 — Profils de Mix, Spatialisation & Pente Rose (`--profile`)

> **Source unique des seuils (v3.1+)** : Toute valeur de Ceiling limiteur (`-1.2 dBTP`), `gl.dist` (`<= 0.04`), tolérance d'accordage (`6 ct` fondation / `12 ct` peak expressif / `6.5 ct` warning master, `8 ct` soundcheck), bornes `lead_vs_harmony` (`[3.0, 9.5] dB`), budget notches (`2`/piste, `3` total) et planchers LPF sources (**jamais de LPF brique 24 dB/oct sous `6.5 kHz` en harmonie / `9 kHz` en lead** ; utiliser un *high-shelf* doux ou un filtre de synthé musical pour assombrir un timbre sans créer de « mix en U »**) est gouvernée **exclusivement** par `config/mix_profiles.json` via `scripts/mix_config.py`. Les corridors spectraux de référence résident dans `golden_references/spectral_corridors.json` (versionné, remplaçant `spectral_profiles.json` comme référence de gate).

Les seuils de `scripts/render_and_audit.py` et `studio_lib.setup_standard_buses()` s'adaptent au profil choisi dans `song_blueprint.json` :

| `mix_profile` | Genres Couverts | Cible LUFS | `low_mids` max | `air` min *(adaptatif `V < -0.2`)* | Sharpness max | Arc Dynamique min | Caractère des Buses (`setup_standard_buses`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`trap_rap`** | Trap, Cloud, Drill, Rap moderne | `-11.0` (`-12..-9.8`) | `<= 24 %` | `>= 3.0 %` *(sombre : **`>= 1.2 %`**)* | `< 1.90` *(sombre `< 1.85`)* | `>= 2.5 dB` | Sidechain Kick→808 doux **`2.4:1`** (`-18 dB`, Release `65–80 ms`), `gl.dist` Soft `<= 0.04` (`Tone <= 7.5k`) avant `gl.eq` LPF, `FX Room` court |
| **`pop_rnb`** | Pop, Synthwave, R&B, Afrobeat, Reggaeton | `-10.5` (`-11.5..-9.5`) | `<= 26 %` | `>= 3.0 %` *(mélancolique : **`>= 1.5 %`**)* | `< 1.95` *(sombre `< 1.85`)* | `>= 2.5 dB` | Sidechain modéré `3:1`, `BUS Music` équilibré, poche voix/lead `1.5–2.5 kHz` |
| **`edm_club`** | House, Techno, EDM, DnB, Rage | `-9.0` (`-10.0..-7.8`) | `<= 24 %` | `>= 3.5 %` *(sombre : `>= 1.75 %`)* | `< 2.05` *(sombre `< 1.90`)* | `>= 2.5 dB` | Sidechain punchy `5:1` sur basse & pads, air contrôlé, sub mono verrouillé |
| **`rock_metal`** | Rock, Indie, Punk, Metal | `-10.0` (`-11.0..-8.5`) | `<= 28 %` | `>= 2.5 %` *(sombre : `>= 1.25 %`)* | `< 2.10` *(sombre `< 1.90`)* | `>= 2.0 dB` | Sidechain léger `3:1`, médiums guitares riches, batterie organique punchy |
| **`lofi_chill`** | Lo-Fi, Boom Bap, Chillhop, Downtempo | `-14.0` (`-15.0..-12.5`) | `<= 34 %` | `>= 0.5 %` *(sombre : `>= 0.25 %`)* | `< 1.75` | `>= 1.8 dB` | LPF chaud `8.5–9.5 kHz` sur `BUS Music`, `gl.dist` saturation douce `<= 0.04`, 0 boost air agressif |
| **`jazz_acoustic`** | Jazz, Soul acoustique, Folk, Bossa | `-14.5` (`-16.0..-13.0`) | `<= 36 %` | `>= 1.0 %` *(sombre : `>= 0.5 %`)* | `< 1.85` *(sombre `< 1.80`)* | `>= 2.5 dB` | **0 distorsion**, compression douce/transparente, `FX Room` naturel (`0.6–1.0 s`) |
| **`classical_ambient`** | Classique, Orchestral, Piano solo, Ambient | `-16.0` (`-18.0..-14.0`) | `<= 40 %` | `>= 0.3 %` *(sombre : `>= 0.15 %`)* | `< 1.75` | `>= 3.0 dB` | **0 distorsion**, sidechain minimal, dynamique ample, `FX Room` profond (`0.8–1.1 s`) |

> **Règle d'adaptation de `air min` à la Valence** : Pour tout morceau sombre (`emotion_vector.valence < -0.2` ou flag `--dark` dans `render_and_audit.py`), réduire le seuil `air min` de moitié (ex. `trap_rap` sombre : **`>= 1.2 %`** au lieu de `3.0 %` ; `pop_rnb` mélancolique : **`>= 1.5 %`**) et durcir `Sharpness max` (`< 1.85`), afin de ne jamais forcer l'éclaircissement artificiel d'un mix sombre.

### Plan Fréquentiel Universel à 5 Bandes
| Bande | Rôle Principal | Isolation & Règles |
| :--- | :--- | :--- |
| **Sub (`20–60 Hz`)** | 808 / Sub / Contrebasse | Mono strict (`Width = 0`), `HPF 28–35 Hz` sur `BUS Bass`. |
| **Bass (`60–250 Hz`)** | Kick + corps de Basse | `HPF 90–145 Hz` sur `BUS Music` (selon profil) ; sidechain ou poche EQ `-2 à -4 dB @ 80 Hz` sur basse. |
| **Low-Mids (`250–500 Hz`)** | Chaleur accords / guitares / piano | Creuser `-3 à -5 dB @ 250–350 Hz` si boueux ; stabiliser M/S `< 650 Hz` si `low_mid_corr < 0.65`. |
| **High-Mids (`500 Hz–4 kHz`)** | Lead, Voix, Snare, Pluck | Relais temporel + EQ miroir `-2 à -4 dB` (`1–3 kHz`) sur Harmonie ; **Lead domine l'harmonie de `>= +3 dB`**. |
| **Air (`4–20 kHz`)** | Hats, Cymbales, Shimmer, Souffle | `LPF 8.5–13.5 kHz` sur `BUS Music` (selon profil/valence) pour adoucir les harmoniques et laisser l'air aux percussions. |

### Cartographie Psychoacoustique des Aigus (`2–20 kHz`) & Loi de la Pente Rose
* **Justification physiologique (Fletcher-Munson)** : le conduit auditif résonne naturellement entre **`2,5 kHz` et `4 kHz`** (sensibilité évolutive maximale aux cris/parole) — un excès y cause une fatigue physique immédiate, alors que `> 10 kHz` reste spatial et non douloureux.

| Sous-zone Aigus | Fréquences | Rôle Musical | Tolérance & Risques |
| :--- | :---: | :--- | :--- |
| **1. Présence / Attaque** | `2–5 kHz` | Clic Kick, claquement Snare, diction vocale. | **Critique (Bas)** : excès = son métallique, strident ("perce-tympan"). |
| **2. Brillance / Détail** | `5–10 kHz` | Sifflantes (*s, ch, t*), *crispness* des cymbales/hats. | **Modéré** : excès = sibilance douloureuse, rendu plastique/artificiel. |
| **3. Air / Soie** | `10–20 kHz` | Légèreté, ouverture spatiale "studio", élévation du mix. | **Élevé** : excès = souffle blanc (*hiss*) ou électricité statique. |

* **Loi de la Pente Rose (`-3` à `-4,5 dB/octave`)** : l'énergie globale des graves vers les aigus doit décroître linéairement (environ `-3` à `-4,5 dB/oct`, comme pluie/vent) — un spectre plat (`0 dB/oct`) sonne agressif et maigre. **Zone cible "tics" rapides (`>= 1/16`) : `7–14 kHz`** (+ HPF `500 Hz–1 kHz`).

| Plage d'Octave | Niveau Cible (Réf. `100 Hz = 0 dB`) | Diagnostic en cas d'écart |
| :--- | :---: | :--- |
| `50–100 Hz` | **`+3` à `+4,5 dB`** | Trop bas = léger/sans fondation · Trop haut = boueux/saturé. |
| `100–200 Hz` | **`0 dB` (Réf.)** | Zone de chaleur et d'impact corporel. |
| `400–800 Hz` | **`-6` à `-9 dB`** | Trop haut = son "carton" (*boxy*), nasal, encombré. |
| `1,6–3,2 kHz` | **`-12` à `-18 dB`** | Trop haut = fatigue auditive immédiate, agression. |
| `6,4–12,8 kHz` | **`-18` à `-27 dB`** | Trop bas = étouffé/sombre · Trop haut = strident/acide. |

### Matrice de Spatialisation 3D (`X` Largeur · `Y` Profondeur · `Z` Hauteur)
| Axe Spatial | Paramètre Technique | Règle d'Application par Catégorie |
| :--- | :--- | :--- |
| **Largeur (`X`)** | Panoramique (`Pan`) & M/S | **Centre strict (`0%`, 100 % Mono `< 120 Hz`)** : Kick, Sub/Basse, Snare, Lead/Voix.<br>**Décalé (`±15–35 %`)** : Hi-hats, Percussions, Arpèges.<br>**Large (`±50–100 %`)** : Pads stéréo, Guitares doublées, Cymbales, Ear Candy. |
| **Profondeur (`Y`)** | Volume + Réverb + LPF | **1er plan (Sec, fort, brillant)** : Lead, Kick, Snare.<br>**2e plan (Réverb courte, `-3 à -6 dB`)** : Rythmique secondaire, Contre-chants.<br>**Arrière-plan (Réverb longue, aigus atténués)** : Pads, Nappes, Ambiances. |
| **Hauteur (`Z`)** | Égalisation (EQ) | **Sol** : Sub & Basse · **Milieu** : Corps & Voix · **Plafond** : Air (`> 10 kHz`), Cymbales, brillance. |

---

## Chapitre 2 — Topologie des Buses (`gl_dsl` + `studio_lib.setup_standard_buses`)

> **Source unique des seuils (v3.1+)** : Toute valeur de Ceiling limiteur (`-1.2 dBTP`), `gl.dist` (`<= 0.04`), tolérance d'accordage (`6 ct` fondation / `12 ct` peak expressif / `6.5 ct` warning master, `8 ct` soundcheck), bornes `lead_vs_harmony` (`[3.0, 9.5] dB`), budget notches (`2`/piste, `3` total) et planchers LPF sources (**jamais de LPF brique 24 dB/oct sous `6.5 kHz` en harmonie / `9 kHz` en lead** ; utiliser un *high-shelf* doux ou un filtre de synthé musical pour assombrir un timbre sans créer de « mix en U »**) est gouvernée **exclusivement** par `config/mix_profiles.json` via `scripts/mix_config.py`. Les corridors spectraux de référence résident dans `golden_references/spectral_corridors.json` (versionné, remplaçant `spectral_profiles.json` comme référence de gate).

`setup_standard_buses(mcp, bpm=..., profile=..., valence=...)` crée les pistes manquantes jusqu'à `22` et configure via `gl_dsl` :
1. **`BUS Drums` (Insert `17`)** : `gl.eq` (`HPF 34 Hz`, shelf `10 kHz` selon profil/valence) + `gl.comp` (`2.2:1`).
2. **`BUS Bass` (Insert `18`)** : `gl.stereo` (`Width = 0.0`) + `gl.comp` sidechain (`1`, preFader Kick ; `2.4:1 / -18 dB / 65 ms` en `trap_rap`, `5:1` `edm_club`, `3:1` pop/rock/lofi, `1.5:1` acoustique) + `gl.eq` (`HPF 30 Hz`).
3. **`BUS Music` (Insert `19`)** : `gl.dist` (`Mix=0.0` acoustique/classique, `<= 0.04` moderne, `Tone<=7500`, `Remove DC=1` — seuil `config/mix_profiles.json`) → `gl.eq` (`HPF 90–135`, shelf `9.5 kHz`, `LPF 8.5–13.5 kHz`, Peak `-2 dB @ 3.2k` si `V<-0.2`) → `gl.comp` glue (`Threshold=-13.5 dB`, préserve l'arc) → `gl.comp` ducking (`Detector=1`) → **2 × `gl.stereo`** (largeur mid/high legacy, `Mono below=250 Hz`, `Dephase=0`). En `trap_rap`/`pop_rnb` sombres, LPF maintenu `>= 11.5 kHz` (garde-fou anti-sur-filtrage `Q3.3`).
4. **`FX Room` (Insert `20`)** : `gl.reverb` via `gl_dsl.reverb_room()` (`Dry=0`, `Dry (full)=0`, `Decay <= T_noire` rythmique ou `0.7–1.1 s` acoustique/ambient ; jamais de send sur Kick/Sub/Hats).
5. **`FX Delay` (Insert `21`)** : `gl.delay` via `delay_send_fx(bpm, valence)` (`Tempo sync`, croche pointée par défaut, `Feedback 20–32 %`, `Dry=0`) + `gl.eq` `HPF 400 / LPF 3500` (`2800` si `V<-0.2`). Sends `delay_send` (`0.08–0.18`) réservés à Lead/Pluck/Counter/Voix.
6. **`FX Hall` (Insert `22`)** : `gl.reverb` Hall via `reverb_hall()` (`Decay 1.6–2.4 s`, `Dry=0`, `HPF 500 / LPF 5500`, `4500` si `V<-0.2`) + `gl.eq`. Sends `hall_send` (`0.14–0.30`) sur Pads/Strings/Ear Candy/Impacts (jamais Kick/Sub/Snare sec).
7. **`Master` (`0`)** : `gl.eq` (`Quality=1`, `HPF 28`, `LPF 17k`) → `gl.comp` (`1.6:1`) → `gl.limiter` via `gl_dsl.limiter_master()` (`Attack=0.002`, `0.2 dB safety=1`, `Ceiling=-1.2 dBTP` — `config/mix_profiles.json`).

---

## Chapitre 3 — Pipeline Loop-First (`É1 → É3`), Quality Gate Conditionnel (`É4`), Stems & Auto-Fix

### 3.0 Audits Incrémentaux Loop-First (`soundcheck_lib`, `vertical_tester`, `horizontal_tester`, `loop_first_runner`)
Tous les nouveaux outils retournent un **contrat JSON uniforme concis** (`verdict`: `PASS|FIX|SWAP`, `score`, `human_diagnosis` 1–3 phrases FR, `next_action` unique) et écrivent les métriques détaillées dans `<proj>/renders/diagnostics/`.

| Étage | Outil MCP / Script | Ce qui est vérifié | Action unique / Garde-fou |
| :---: | :--- | :--- | :--- |
| **É1** | `audio-analyzer:audit_track_soundcheck` (`scripts/soundcheck_lib.py`, `scripts/listener_backend.py`) | Rendu headless `2–4` mes. par piste isolée : RMS `> -42 dBFS`, justesse jouée `<= 8 ct` (soundcheck), intégrité chromatique, `0` pic `> +5 dB` (`2–5 kHz`) ni glitch, enveloppe vs BPM, fidélité `valence`. | `CHECK_ROUTING_OR_SWAP`, `ADJUST_FINE_TUNE`, `APPLY_LPF`, `ADD_NOTCH_EQ`, `ADJUST_ADSR` · `export_palette_preview.py` (`palette_preview.wav`). |
| **É2** | `audio-analyzer:audit_vertical_step` (`scripts/vertical_tester.py`, `song_builder.py --core-only`) | Paliers cumulatifs sur `core_loop.gridline` (`4/8` mes.) : `V1_LOW_END` (overlap/phase `<150 Hz`), `V2_GROOVE` (sharpness/mitraillette, sauté si `has_drums=false`), `V3_HARMONY` (conflit chroma CQT basse/accords + `low_mids`), `V4_LEAD` (hiérarchie `>= +3 dB`, pente rose, `spectral_match`). | **Isolation** : `forbidden_tracks` verrouillé (`loop_first_runner.py` bloque toute édition hors couche $k$ ; escalade `SWAP_SAMPLE` après 2 échecs) → `core_loop_locked = true`. |
| **É3** | `audio-analyzer:audit_horizontal_seam` (`scripts/horizontal_tester.py`) | Fenêtres `4` mes. (`±2` mes.) sur transitions majeures + `Outro` : contraste `ΔLUFS`, événement (`beat4_dropout` / `filter_sweep`), `0` glitch `±150 ms`, RMS dernière seconde Outro `< -45 dBFS`. | `INCREASE_SECTION_CONTRAST`, `ADD_TRANSITION_EVENT`, `CLEAN_SEAM_FADE`, `APPLY_OUTRO_FADE` via `transitions_lib.py`. |

### 3.1 Critères `render_and_audit.py` + `analyze_stem_hierarchy` + `spectral_match_score`
* **Checklist Structure & Timbre Pré-Rendu** : `100 % Mono < 120 Hz` · aucune note `< 100 Hz` plus courte que `1/8` (`Release < 50 ms`) · `1–8` pistes simultanées par mesure · Lead dominant de `>= +3 dB` · aucune boucle de 8 mesures identique · isolation strictement `1 sonorité = 1 piste` · aucun pic isolé `> +4 dB` dans `2,5–4,5 kHz` · pente rose `-3..-5 dB/oct` · transitoires décalés `>= 5 ms` (`groove_lib`).
* **12 critères Quality Gate** (valeurs gouvernées exclusivement par `config/mix_profiles.json` via `mix_config.py`) : (1) `clipped == 0`, (2) `true_peak <= ceiling+0.2` (`-1.0 dBTP`), (3) `|lufs - target| <= 1.2`, (4) `low_end_corr >= 0.50`, (5) `low_mid_corr >= 0.55`, (6) `sharpness < max`, (7) `micro_glitches == 0`, (8) `resonances == []`, (9) `low_mids <= max %`, (10) `air >= min %`, (11) `|tuning_offset| <= 6.5 ct` (warning master ; fondation 6 ct), (12) `dynamic_arc >= min dB`.
* **Résonances « notchable » uniquement** (`deep_diagnostics.classify_resonances`) : un pic 1/3 d'octave ne déclenche la gate / l'auto-fix `EQ_RESONANCE_NOTCH` que s'il est **étroit** (`peak_width_bands <= 2`) **et** fort (`local_excess_db >= +6 dB`). Les plateaux larges (formantes, pads) sont du contenu musical : `third_octave_resonance_candidates` les rapporte sans les toucher. Le check 2–5 kHz du soundcheck applique la même règle de largeur.
* **Son naturel (anti-« électronique/écrasé »)** : master limiter `Gain <= 5.5 dB` / `Ceiling -1.2 dBTP`, comp master `-12 dB / makeup 0.8`, `gl.dist` BUS Music `Mix <= 0.04, Drive 2.5` (tous ces seuils : `config/mix_profiles.json`), LPF BUS Music >= 12 kHz, shelf aigus batterie `+1.2 dB @ 10 kHz` max. Palette A organique favorisée (bonus 0.09) quand des samples réels existent. **Gate `low_end_share`** : sub+bass <= `low_end_max` % (42 % trap_rap, voir `GENRE_GATES`) sinon auto-fix : BUS Bass `-1.5 dB` (max 2 passes) + HPF BUS Music 160 Hz. Mesuré sur master_v9 : 56 % d'énergie < 120 Hz et air 1,5 % = mix boueux/sombre.
* **Floors de présence** (`gain_staging_lib.ROLE_PRESENCE_FLOOR_DB`) : lead >= -3 dB, chords_pad >= -10 dB, etc. vs l'ancre — une piste ne peut plus devenir imperceptible ; compensation **+2 dB si centroid > 4 kHz** (cloches/aigus perceptivement faibles). Le check vertical V4 exige toujours lead >= harmonie + 3 dB.
* **Audit musical projet-wide du blueprint (`scripts/blueprint_safety.py`)** : `blueprint_musical_safety_issues(bp, catalog_path)` contrôle **chaque piste `source_type="sample"` avant build/rendu** : `sample_path` + `root_note` obligatoires, cross-check `root_note` vs `midi_root` détecté dans `samples_catalog.json` (projet) ou `samples.db` racine (SSOT, table `sound_assets` — lu directement par `blueprint_safety.load_catalog_index`) (même classe de hauteur requise — erreur d'octave inoffensive, erreur de classe = dissonance, avec `recommended_root_note`), sample `playable_chromatically == false` (accord/séquence) interdit en rôle mélodique, key-fit vs la gamme (mode-aware via `harmonic_core.mode`). Tourné dans `song_builder.load_blueprint` (`meta["musical_safety"]`), après `apply_palette_to_blueprint` (`loop_first_runner`, état `pipeline_state.json`) et dans le **quality gate** de `render_and_audit` : rapport `musical_safety`, et toute issue `severity="error"` bloque le rendu (`gate_failures += musical_safety[...]`). Rôles percussifs et boucles exemptés du key-fit.
* **v3.0 — plan de refonte complet** : verrou d'octave exact `root_note == midi_root`, plafond `S_max` 5/7 st, boucles `melodic_loop` (key-fit CQT + verrou BPM 0,05/r_eff [0,88;1,14]), `kick_tuning_guard` + confinement cloches {1,5,8}, Low Interval Limit, autofix contour-preserving, `sanitize_note_overlaps` final, source unique `config/mix_profiles.json` (LPF sources >= 6,5/9 kHz anti « mix en U », notches 2/3 -> SWAP_SAMPLE, justesse 6 ct stems / 12 ct vibrato), `stem_matrix_evaluator` (1 rendu stems 8 mesures -> V1..V4 + solveur de gains en NumPy), `compile_song.py` 100 % déclaratif, 3 macro-outils MCP, `docs/AGENT_RUNBOOK.md` (< 6 000 cars) + `docs/REFERENCE_ARCHIVE.md`. Tests : `tests/test_pitch_and_loop_safety.py`, `test_harmonic_realism.py`, `test_mix_coherence_dag.py`, `test_3turn_pipeline_budget.py`, `test_docs_hygiene.py`.
* **v3.1 — spécifications techniques (6 chantiers, doc `audit_spécifications_techniques_ai_studio_v3_1.md`)** : **source unique renforcée** — 100 % des seuils de mix/gate passent exclusivement par `config/mix_profiles.json` via `scripts/mix_config.py` (`get_profile_config`, `tuning_tolerance`, `notch_budget`, `stem_hierarchy_bounds`, `autofix_step_bounds`) ; autofix **transactionnel** (`scripts/autofix_transaction.py` : paliers 1/2/3, rollback immédiat si un invariant de Palier 1 régresse, blacklist + journal `renders/autofix_history.json`) ; **ScoreIR** (`scripts/score_ir.py` + `schemas/score_ir.schema.json`, artefact debug `<projet>/score_ir.json`) : le groove (swing/strum/Haas/AMG) s'accumule dans `expression` sans toucher `grid_beat`/`pitch`, et le **clamp de frontière harmonique** (guard 20 ms) garantit 0 clash b2 / 0 chevauchement 808 post-groove ; couplage timbre↔voicing (`adapt_voicing_to_timbre` : confinement {1,5,8} cloches, drop-2/shell, LIL) + audit audio chroma-vs-grille par segment (`stem_matrix_evaluator.evaluate_barwise_harmonic_alignment`, action `FIX_TRACK_ROOT_NOTE` câblée dans `multiscale_autofix`) ; **corridors spectraux versionnés** `golden_references/spectral_corridors.json` (7 profils, bark24 p10/p50/p90 + 8 métriques + `dark_valence_overrides`, dead-zone corridor loss) avec repli 3 niveaux (`reference_tier` 1 = audio utilisateur / 2 = corridor versionné + taste ≤ 30 % / 3 = baseline psychoacoustique, `reference_provenance` toujours renseigné) ; hygiène des banques (`scripts/asset_hygiene.py` : whitelist d'extensions audio + magic bytes + parasites OS ; **pas de détection de licence/copyright** — responsabilité de l'utilisateur). Tests : `test_config_single_source.py`, `test_post_groove_harmony.py`, `test_autofix_rollback.py`, `test_offline_corridors.py`, `test_soundbank_hygiene.py`.
* **Équilibrage des volumes** : `balance_tracks_in_session` (in-session, sans .wav, trims ±6 dB vers cibles de rôle, ancrage -16 LUFS actifs via `ROLE_TARGET_OFFSETS_DB`) ; repli `apply_nominal_balance` dans `song_builder` quand aucune métrique soundcheck n'existe (évite les faders par défaut inégaux).
* **Humanisation rythmique** (`groove_lib`) : `apply_anti_machine_gun(..., humanize_sparse=True)` appliqué à **toutes** les pistes dans `song_builder` (accent métrique doux sans jitter sur les notes tenues) ; `apply_swing` décale les croches off-beat de `swing × 0.25` temps ; `apply_haas_pocket` plafonne chaque décalage à **5 % du temps de beat** (anti-flou aux tempos élevés).
* **Garde-fou sans batterie (`has_drums == False` ou `classical_ambient`)** : passer `--no-drums` ou `--blueprint song_blueprint.json` ; désactive l'obligation `dynamic_arc` (adapté aux crescendos continus sans drops batterie) et filtre les faux positifs inter-harmoniques sous `150 Hz` ou `> 6 kHz` (l'analyse tiers d'octave exclut aussi les pentes monotones).
* **Hiérarchie des Stems (`render_project_stems` + `analyze_stem_hierarchy`)** : vérifie `lead_vs_harmony` dans `[3.0, 9.5] dB` (`stem_hierarchy_bounds` config), `kick_bass_transient_overlap <= 0.15` et `pink_slope_db_per_oct` (`-3..-6.5 dB/oct`).
* **Référence Spectrale (`scripts/bootstrap_golden_refs.py`)** : `golden_references/spectral_corridors.json` (versionné) compare la distribution Bark 24 bandes + 8 métriques aux corridors `[P10,P90]` du profil via `corridor_score`/`spectral_match_score` (`0..1`, 0 PyTorch) ; `spectral_profiles.json` reste le format legacy du builder.

### 3.2 Boucle `--auto-fix --max-passes 2` & Table Symptôme → Action Unique
En cas d'échec, **`render_and_audit.py --auto-fix --max-passes 2`** applique automatiquement les fixes déterministes (`gl_dsl.eq_band` Peak `-3.5..-4.5 dB` sur résonances/`280 Hz`, ajustement `Master Gain`, abaissement `Limiter Ceiling`), re-rend et journalise chaque passe dans **`renders/autofix_history.json`**.

| Symptôme (`failures`) | Action Unique (`gl_dsl` / Python) |
| :--- | :--- |
| **`clipped > 0` / `true_peak > -1.0`** | `limiter_master(gain, ceiling - 0.4)` max **4 drops** (floor -3.0) avec `Attack (look-ahead)` **calibré 0.005 s** (`config/gl_calibration.json`, `gl_dsl.calibrated_lookahead_s()`) ; si plafond atteint, fallback `Gain -= clamp(tp-limit+0.3, 0.5, 4) dB` (`true_peak_gain_fallback`). |
| **`lufs` trop bas / haut** | Ajuster `Master slot 3 Gain` de `±Δ` (`\|Δ\| <= 4 dB`/passe, plafond `-1.2 dBTP`). |
| **`low_end_share > max %`** (v4.3 fix : lecture via `mix_config.summary_bands_pct` — `bands_pct` plat, pas `spectral_bands` imbriqué) | Trim `BUS Bass volume` **proportionnel à l'excès** (`-clamp(excess*0.12, 1.5, 4) dB`, max 3 passes, floor 0.25) + `HPF 160 Hz` posé sur un **vrai slot `gl.eq`** de BUS Music (jamais un index de slot codé en dur). |
| **`resonances = [F]` avec F < 200 Hz** (v4.3 règle 3b : 63/80 Hz d'une 808) | `gl.eq` **BUS Bass** uniquement : Peak `-3..-6 dB @ F` (`Q=5`) + `Band 1 HPF 34 Hz enabled=1` ; budget de notchs partagé (max 3). Jamais de notch 200–800 Hz (fondamentaux musicaux). |
| **master comp hors invariants** (v4.3 règle 2b : Threshold < -12 dB ou Makeup > 0.8 dB) | Clamp `mixer/0/slot/<comp>/Threshold >= -12 dB`, `Makeup <= 0.8 dB` (`master_comp_clamp`, une fois). |
| **`low_end_corr < 0.50`** | `BUS Bass Width=0.0`, Kick `pan=0`, `fx_send=0` sur Kick/Basse, `BUS Music HPF >= 120`. |
| **`stereo_width < min dB`** | `WIDEN_HARMONY_OR_PERC` : `gl.stereo` legacy (`Width 2.0`, `Side level 6-12`, `Mono below 250`) + sends `FX Hall` `+0.06` sur Chords/Pads. |
| **`stereo_balance > 1.2 dB`** | `FIX_STEREO_BALANCE` : recentrer/nudge `±0.10-0.16` pan sur 1–2 inserts panoramiques, max 2 passes. |
| **`lead_vs_harmony` hors `[3.0, 9.5]`** | `ADJUST_TRACK_FADER(target_track, delta_db)` borné `±4 dB/passe` (cumul `8 dB`), `forbidden_tracks` respectés en É2. |
| **`low_mid_corr < 0.55`** | `stabilize_stereo_low_mid()` sur le sample ou `mixer/N/stereo = -0.35`. |
| **`sharpness >= max`** | Vérifier Lead `<= MIDI 74`, filtre de synthé musical `5–7 kHz` (pente douce, jamais LPF brique 24 dB/oct < 9 kHz lead), Peak `-3.5 dB @ 3.2 kHz` (`q=1.4`). |
| **`micro_glitches > 0`** | `audit_project_sources.py --prepare-samples`, `fade_in >= 2 ms`, hats `Attack >= 4 ms`, `LPF 13 kHz`. |
| **`resonances = [F]`** | `eq_band(slot, "peak", F, -4.0, q=2.4)` sur l'insert dominant (`Chords`/`Bass`/`HiHat`/`BUS Music`). |
| **`low_mids > max %`** | `BUS Music slot 2` : `HPF 140 Hz` + Peak `-4.0 dB @ 280–330 Hz` (`q=1.2`). |
| **`air < min %`** | Si `V >= 0` : `BUS Drums` `+1.5 dB @ 10 kHz` / ouvrir LPF · Si `V < 0` : passer `valence`/`--dark` et relever hats `+1 dB`. |
| **`tuning_offset > 6.5 ct`** (warning master ; fondation 6 ct / expressif 12 ct — config) | Recalibrer `Root note` + `Fine tune` (`measure_fft_tuning()`) ou annuler detune/chorus excessif. |
| **`dynamic_arc < min dB`** | Alléger Intro/Pont (`Matrice 1.3`), `BUS Music comp Threshold >= -13.5`, `transitions_lib.section_gain_arc`. |

### 3.2b — Analyse des causes racines d'un rendu « boomy / inaudible » (audit v4.3)

Audit fondé sur un morceau réel (`rap_nocturne_2min`, trap_rap 136 BPM, profil `trap_rap`) dont le master
mesurait `low_end_share=83%` (83 % de l'énergie sous 250 Hz), `high_mids=3%`, `air=1.5%`, résonances 63/80 Hz,
`true_peak=+0.7 dBTP`, `LUFS=-9.7` vs cible -11, et un `autofix_history.json` bloqué (`final_passed=false`,
aucune mutation low-end appliquée). Douze causes racines diversifiées, et leur correctif :

1. **Règle autofix low-end morte (clé de summary)** : `_apply_autofix_mutations` lisait `summary["spectral_bands"]["sub"]["energy_pct"]` alors que le summary n'expose que `bands_pct` (plat) → `low_end_share_val` valait toujours 0.0 → le trim du BUS Bass et le HPF de BUS Music ne se déclenchaient **jamais**. Fix : `mix_config.summary_bands_pct()` (lit `bands_pct`, fallback `spectral_bands`), trim proportionnel à l'excès (max 3 passes), HPF posé sur un vrai slot `gl.eq`.
2. **Résonances sub < 200 Hz incorrigeables** : la règle 3 ne notchait que [200, 8000] Hz (puis >= 800 Hz) alors que le gate échoue sur **toute** résonance en profil avec batterie → 63/80 Hz bloquaient le gate à jamais. Fix : règle 3b dédiée au BUS Bass (Peak -3..-6 dB @ F, Q=5, HPF Band 1 34 Hz actif).
3. **True peak ingérable** : plafond de 2 drops de ceiling × 0.4 dB sans fallback → TP 0.7 → -0.1 dBTP toujours > limite -1.0. Fix : 4 drops + fallback par réduction du `Gain` du limiteur.
4. **Look-ahead du limiteur non calibré** : `Attack (look-ahead)=0.002` (2 ms) en dur dans `limiter_master`, alors que `config/gl_calibration.json` mesure 5 ms → les transitoires de 808 dépassaient le plafond. Fix : `gl_dsl.calibrated_lookahead_s()` (0.005 s) appliqué par défaut et dans `setup_standard_buses`.
5. **Master comp hors invariants** : Threshold -16 dB / Ratio 2.2 / Makeup +5.5 dB dans le projet (invariants : -12 dB / 1.6 / 0.8) → mix écrasé, pumping, le limiteur se battait contre le makeup. Fix : règle `master_comp_clamp`.
6. **Vélocités plates** : `vel = gain*110` sans humanisation → 543 hats en doubles croches à 136 BPM tous à 110 = mitraillette harsh 2–5 kHz, et lead à 62 noyé sous la batterie. Fix : humanisation déterministe (seed fixe : accent temps forts +6, off-beats -6, jitter ±4), plafonds par rôle (hats/percs <= 96, kick/snare <= 118), floor lead >= 72 (`strudel_to_score_ir.haps_to_tracks`).
7. **Arc d'énergie de sections plat** : `structure[*].energy = 0.7` partout → morceau sans relief, gate `dynamic_arc >= 2.5 dB` KO. Fix : `blueprint_enricher.normalize_flat_energy_arc` (arc par type de section modulé par `emotion_vector.energy`).
8. **Cutoff du lead sous le floor SSOT** : `plugin_params.cutoff_hz=4200` pour un lead (floor `source_lpf_floor_hz.lead = 9000` Hz) → lead assourdi/inaudible. Fix : clamp automatique (`blueprint_enricher.clamp_cutoff_lpf_floors`) + warning `cutoff_below_lpf_floor` dans `blueprint_safety`.
9. **Noms de canaux dupliqués** : `"hat" in role` matche `open_hat` aussi → deux canaux nommés `HiHat`, ce qui casse `_infer_roles()` et l'autofix par nom. Fix : branche `OpenHat` dédiée avant `HiHat` (pan distinct 0.30).
10. **BUS Bass chargé en low-mids** : EQ du bus avec boost +2.2 dB @ 175 Hz et HPF Band 1 désactivé, 808 jouée en MIDI 36–46 avec tenues 1.5 temps + glide → masque tout le reste. Fix : règles 6b/3b (trim, HPF 160 Hz BUS Music, HPF 34 Hz + notchs BUS Bass) + sidechain Kick→BUS Bass déjà en place (Ratio 2.4, -18 dB).
11. **Palette faible** : `composite_score=0.369`, 55 samples rejetés par `quality_gate_v2`, `p_fallback=0.33` → peu de bons candidats, thème `hybrid` avec samples imparfaits. Mitigation : `pleasantness_caps` (crest) et vérifier `samples_catalog.json` (`index_project_samples.py`) avant de forcer `palette_id="auto"`.
12. **Handoff sans master** : `master=n/a` quand le `.gridline` est absent ou `auto_master` off → ne pas juger la qualité depuis le handoff seul ; lancer `render_and_audit.py --auto-fix` pour produire `renders/master.wav` + `autofix_history.json`.

**Lecture du diagnostic** : `low_end_share` = sub(20–60) + bass(60–250 Hz) ; > 42 % = boomy/muddy et le reste du mix
est masqué (inaudible sur enceintes laptop qui ne reproduisent pas < 250 Hz). `air` < seuil = son bouché.
Toujours croiser `autofix_history.json` (mutations réellement appliquées) avec `quality_gate.failures`.

#### Round 2 (v4.3) — durée des accords, timbre « électrique », niveaux inégaux (projet `rap_melodique_2min`)

Nouvelles causes racines (en plus des 12 ci-dessus), avec correctifs :

13. **Accords/pads trop courts** : `release_ms=260` (gl.keys) sur des lignes tenues d'une mesure
    (1.76 s à 136 BPM) + `allow_bleed=false` -> le `boundary_clamp` coupe le son pile à la barre
    suivante. Fix : floors de release par rôle (`blueprint_enricher.enforce_sustain_release_floors`,
    chords >= 800 ms, pad >= 1000 ms, lead >= 300 ms), `release_ms` relevé dans
    `config/timbre_archetypes.json`, et `allow_bleed=True` sur les notes `chords`/`pad`
    (`strudel_to_score_ir.build_score_ir_from_strudel`).
14. **Pad/lead « électriques » en mode sombre** : les `dark_overrides` de `trap_rap:pad`/`lead`/
    `counter` basculaient sur `gl.wavetable "Formant Choir"` / `gl.additive "Masked lead"` /
    `"Glass Bell"` (sons synthétiques métalliques). Fix : les overrides sombres ne changent plus
    de plugin/preset (on garde `gl.analog Velvet Pad` / `Glass Pluck` / `Harp pluck`), seuls le
    cutoff et la release sont ajustés.
15. ** mauvais sample pour arp/counter** : `Steel Stab One Shot` (`sample_nature=inharmonic_bell_or_fx`,
    nom « stab ») choisi via le slot `lead_melody` où la cloche n'était que « discouraged ».
    Fix : `sample_fit_guard.nature_verdict(..., valence)` interdit la cloche sur `lead_melody`
    quand valence < -0.2, et `candidate_policy` exclut les noms électriques durs (stab/metallic/laser…)
    du lead en mode sombre (malus renforcé −0.35).
16. **Dérivés non adaptés au projet** : les recettes `sample_prep_lib` ne filtraient que si
    `r_harsh > 0.32`. Fix : `index_project_samples._suggest_prep_recipes` détecte désormais
    `inharmonicity > 0.04` ou un nom électronique sur rôle tonal -> LPF au plafond du rôle
    + coupe -3 dB @ 5.2 kHz + saturation chaude 1.15.
17. **Niveaux inégaux (chords inaudibles)** : `gain_staging_report.json` montrait Chords
    0.68 -> 0.4795 (−6.07 dB) car `ROLE_TARGET_OFFSETS_DB["trap_rap"]["chords_pad"]` visait -8 dB
    vs kick. Fix : target relevé à -6 dB (range -9..-4) et floor de présence chords_pad -10 -> -8 dB.

### 3.3 v4.1 P3 — Calibration réelle `gl.*`, propriété mixer unique & micro-passe scalaire
* **Calibration réelle du surrogate** : `scripts/calibrate_gl_plugins.py` (à exécuter **une fois par version de Gridline** sur la machine hôte, binaire `gridline` requis) mesure la vraie réponse de `gl.eq` (boost +6 dB @ 1 kHz sur bruit, ratio FFT), la coloration de `gl.comp` et le dépassement réel de `gl.limiter`, puis écrit **`config/gl_calibration.json`** (versionné, stats seules, jamais d'audio) : `eq.gain_scale {low,mid,high}`, `eq.q_scale`, `limiter.ceiling_offset_db`, `measured: true/false`. Sans binaire (CI/sandbox) le script écrit la calibration idéale (`measured: false`, échelles 1.0) et `--check` valide la structure en CI. `math_mix_solver.load_calibration()` lit ce fichier (mis en cache, repli idéal si absent) : les gains EQ du modèle sont mis à l'échelle des vraies mesures `gl.eq` et le plafond limiteur est décalé de `ceiling_offset_db` (toujours autour de `-1.2 dBTP` via `config/mix_profiles.json`).
* **Matrice de propriété stricte (un seul propriétaire par paramètre)** : après `math_mix_solver.py`, `mark_solver_applied(<projet>, solution)` (CLI `--set-flag`) écrit `math_mix_solver_applied: true` dans `<projet>/pipeline_state.json`. Le solveur possède alors **exclusivement** faders, EQ inserts, pans, largeur, sends FX et limiteur. `autofix_transaction.apply_with_rollback` retourne `SKIPPED_SOLVER_OWNERSHIP` pour toute action hors `SOLVER_SAFE_FIXES` = {`SWAP_SAMPLE`, `INVERT_808_POLARITY`, `SHIFT_BASS_MICRO_MS`, `TRIM_SAMPLE_START_MS`, `FIX_TRACK_ROOT_NOTE`} (invariants durs de sécurité Palier 1 uniquement) — une passe post-solveur ne peut plus défaire l'optimum conjoint.
* **Micro-passe scalaire résiduelle** (`math_mix_solver.scalar_master_fix(metrics, profile)`, 0 token, 0 ré-optimisation) : après le rendu master post-solveur, `Master Gain` ajusté **uniquement** si `|LUFS − target| > 0.7 dB`, `Ceiling` du limiteur abaissé **uniquement** si `true_peak > -1.0 dBTP`. Jamais de fader de piste ni d'EQ.
* **Corridors externes anti-auto-référence (P4)** : `scripts/build_external_corridors.py` analyse 3–5 masters commerciaux par profil déposés dans `golden_references/external_masters/<profil>/` (audio **jamais versionné**) et écrit les stats seules agrégées (Bark24 + LUFS/LRA/crest/pente rose/largeur M/S/ratios de bandes, `[P10,P50,P90]`) dans `golden_references/spectral_corridors_external.json` (provenance `external_pro_reference`) — le fichier versionné `spectral_corridors.json` n'est jamais écrasé. `math_mix_solver.load_corridors` préfère automatiquement ce fichier externe quand il existe (`reference_provenance` renseigné), ce qui brise définitivement la convergence vers la moyenne des anciens mixes internes.
* **Proxy PQ rapide (P4)** : `scripts/pq_proxy.py` entraîne un régresseur sklearn (Ridge) en < 10 ms de prédiction (0 GPU) sur le vecteur `(métriques DSP + LRA + crest + MelodicAppealIndex + ratio de mutation)` des morceaux déjà évalués en PQ (`memory/pq_dataset.jsonl`, commande `record`), utilisable dans le tournoi de variantes et en CI (`check` vérifie < 10 ms) ; sans modèle, `predict()` renvoie `None` (dégradation propre, jamais d'exception).
* **Budget de mutation symbolique (P4)** : `strudel_to_score_ir.py` mesure la mutation (notes déplacées/repliées/supprimées, `|Δpitch| >= 12` st + suppressions) **après** `expand_chord_symbols` et **avant** les garde-fous ; au-delà de `MUTATION_RATIO_MAX = 0.25` (25 %) d'une piste, le rapport émet le diagnostic 1 ligne `ERR_OVER_REPAIRED <role>: N% notes shifted` au lieu de valider silencieusement une partition rabotée (Goodhart : la mélodie perd son contour naturel).

---

## Chapitre 4 — Playbook des Pièges Techniques Vérifiés (Ce qu'il faut retenir)

*(Les erreurs manuelles de Q `Band N width`, `gl.reverb (full)`, `gl.limiter Attack`, `sidechain`, `db_to_auto` et enveloppes `gl.*` sont désormais éliminées automatiquement par **`scripts/gl_dsl.py`**.)*

| Piège Vérifié | Conséquence | Règle Définitive |
| :--- | :--- | :--- |
| **`gl.multisampler` (`.sfz`) en GUI** | Silencieux (`-144 LUFS`). | Utiliser `gl.sampler` WAV 24-bit (`440 Hz`) ou `gl.*` natif. |
| **`patch mixerTrack` / `set_song bpm`** | Mauvais routage / tempo ignoré. | Utiliser `song_builder.py` (`edit_channel`, `set_song tempo`). |
| **`loudnorm` sur one-shots `< 2.5 s`** | Écrase transitoires Kick/Snare. | `clean_audio_sample` (peak `-1.0 dBFS`, fades `2/15 ms`). |
| **`pitch_shift` sur 808 `< 80 Hz`** | Dérive `-35 ct`, attaque molle. | Régler `Root note` + `Fine tune` dans `gl.sampler` (`+X` monte de `+X ct`). |
| **808 mitraillées / infra-sub `< 42 Hz`** | Pics saturation & `-13.5 LUFS`. | `1–2` notes tenues/mes. (`>= MIDI 31`, `36–42` en `C..F#`), `Release 50 ms`, `enforce_frequency_inertia`. |
| **Phase dégradée post-`pitch_shift`** | `low_mid_corr` chute (`0.09`). | `stabilize_stereo_low_mid(..., cutoff_hz=700)` après `pitch_shift`. |
| **Cloche `mono_oneshot` depuis FX** | Un FX rééchantillonné garde des lobes faux (`-15 ct`). | Varispeed sur le noyau tonal stable seul (`0.14–0.39 s`) + decay exponentiel. |
| **Glissando d'attaque / FX faux** | Fausse le diapason global (`-17 ct`). | Couper glissando d'attaque et accorder aussi les FX tonaux (`440 Hz`). |
| **`gl.sampler` signe `Fine tune`** | Offset inversé double l'erreur (`±18 ct`). | `+X` monte (`+X ct`), `-X` baisse (`-X ct`) ; utiliser `measure_fft_tuning()`. |
| **Espace Rap & Collage pré-limiteur** | Pistes vocales / transitoires bloquent `-13 LUFS`. | Zéro vox en instru Rap, creux `1–2.5 kHz` (`-3.5 dB`) sur `Chords/Pad`, `30–45 %` silences Lead. |
| **`add_automation` sans `"track"`** | Automation créée non placée (`0` clips). | Toujours passer `"track": <idx_playlist>` (`transitions_lib.py` le gère). |
| **Collision `_infer_roles()` MIDI** | `"Vocal"` fusionné avec `Lead`, `"Piano"` arpégé pris pour `Lead`. | Respecter la table Facette 3 §5.2 (`Bass`, `Chords Piano`, `Lead`, `Counter Harmony`). |

---

## Chapitre 5 — Journal de Références Compact (Capitalisation)

> **Règle** : Consigner chaque nouveau morceau validé sur **1 seule ligne** pour garder ce document léger en tokens.

| Projet | Genre / BPM / Key | Durée | LUFS / dBTP | Phase (`<150` / `200-800`) | PQ | Points Clés Validés |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `active6` *Airey Horizon* | Trap / — | `60 s` | `-11.83` / `-1.50` | OK | `7.12` | Intro `audit_project_sources`, `studio_lib`, `render_and_audit` |
| `active7` *Midnight Drift* | Trap / `150` / `Bbm` | `45 s` | `-10.71` / `-1.40` | `—` / `0.707` | **`8.08`** | Arc dynamique réel `3.8 dB` |
| `active8` *Simba d'Ea'alah* | VGM→Rap / `144` / `Em` | `107 s` | `-11.43` / `-1.82` | OK | `7.46` | 100 % stems `htdemucs_6s` |
| `active9` *Glass Horizon* | Trap / `172` / `Fm` | `181 s` | `-12.12` / `-1.13` | OK (`+1.0 ct`) | `7.23` | Format long 3 min |
| `roshi_serpent` *Pigalle* | Dark Trap / `136` / `E Phryg` | `121 s` | `-11.48` / `-1.38` | OK | `7.39` | Fix `gl.eq Peak=6`, Freesound API, `gl.sampler` WAV |
| `morceaux/rosh2` *Astral Pigalle* | Cloud/Dark / `136` / `Cm` | `120 s` | `-11.86` / `-1.21` | `1.00` / `0.943` | **`7.96`** | 100 % CC0 `mono_oneshot`, 0 glitch/résonance, arc `>4.5 dB` |
| `morceaux/ryan` *Tokyo Expressway 1993* | Cloud Phonk / `192` / `Am` | `121.7 s` | `-13.27` / `-2.35` | `0.95` / `0.825` | **`7.80`** | `lofi_chill`, `96` half-time, Lead Keys `C4-D5` LPF `2.6k`, Pente Rose OK |
| `morceaux/song1` *Ombre Phrygienne* | Dark Trap / `142` / `C Phryg` | `121.7 s` | `-12.14` / `-1.62` | `1.00` / `0.898` | **`8.05`** | Samples `morceaux/SAMPLES`, `440.25 Hz` (`+1 ct`), 0 glitch/résonance, arc `7.04 dB` |
| `morceaux/song3` *Sillage de Minuit (Rap Inst.)* | Dark Trap / `144` / `Gm` | `120.0 s` | `-12.05` / `-1.32` | `1.00` / `0.922` | **`7.47`** | Instru Rap sans vox (`song3/Samples/` + alts `samples/`), 808 longues `1-2/mes`, `440.0 Hz (0.0 ct)`, 0 glitch/résonance, arc `4.44 dB` |
| `morceaux/song4` *Ondes de Verre (Rap Inst.)* | Dark Trap / `172` / `Fm` | `178.6 s` | `-12.18` / `-1.29` | `1.00` / `0.990` | **`7.28`** | 100 % samples fournis (`air/Samples/` + 22 alts), 22 pistes isolées, `440.51 Hz (+2.0 ct)`, 0 glitch/résonance, arc `10.19 dB`, centralisation `sample_prep_lib` + `song_builder` |
| `morceaux/song5` *Brume d'Automne (Rap Inst.)* | Melancholic Trap / `134` / `Dm` | `122.4 s` | `-11.59` / `-1.08` | `0.999` / `0.730` | **`7.44`** | 100 % samples fournis (`Untitled/Samples/` + 13 alts) + `gl.*`, 15 pistes isolées, `440.0 Hz (0.0 ct)`, 0 glitch/résonance, pente `-4.44 dB/oct`, `spectral_match=0.894` |


---

## Phase 6 — Synchronisation $S1/S2$, Pyramide $H \times V$, Cache & Auto-Fix Déterministe

* **Micro-timing & Phase ($S1 / S2$)** :
  * `S1/V` (`analyze_kick_bass_transient_phase`) : détecte les inversions de polarité Kick/808 (`INVERT_808_POLARITY` via `sample_prep_lib.invert_phase=True` ou `invertPolarity` mixer) et les micro-décalages (`SHIFT_BASS_MICRO_MS` borné à `±10 ms`).
  * `S1/H` (`analyze_sustained_spectral_flux`) : signale les timbres soutenus figés (`STATIC_TIMBRE`).
  * `S2/H` (`analyze_audio_pocket_alignment`) : mesure le retard d'attaque audio vs MIDI à la milliseconde et rogne les silences pré-attaque (`TRIM_SAMPLE_START_MS` via `sample_prep_lib.trim_start_ms`).
  * `S2/V` (`analyze_transient_stacking`) : détecte `> 2` attaques non-percussives simultanées dans `1–4 kHz` / `LowMid` (`STAGGER_ATTACK_OR_STRUM` via `groove_lib.apply_micro_strum` ou enveloppe synthé `gl_dsl`).
* **Surcharge par contrat & empreinte finale (`scripts/render_and_audit.py`)** : `--contract <multi_scale_contract.json>` surcharge localement `target_lufs`, `low_mids_max`, `air_min`, `sharp_max` et compare `bark24`; tout `PASS` complet exécute `macro_structure_audit.py` et exporte `<projet>/renders/master_fingerprint.json`.
* **Workflow économe en tokens** :
  * Préférer `audio-analyzer:audit_multiscale_hv(project_dir, stage, contract_path)` ou le CLI one-shot `.venv/bin/python scripts/run_autonomous_pipeline.py --project <dir> --stage E1|E2|E3|E4|ALL --auto-fix --max-autofix-passes 2 --json-compact`.
  * Sortie strictement bornée par `scripts/multiscale_pyramid.py` (`<= 1 500` caractères, `<= 400` tokens, Top 3 déviations, blocage en cascade $S1/S2 \Rightarrow S4=S5=\text{null}$), détails complets écrits dans `renders/diagnostics/`.
  * Cache SHA-256 incrémental (`scripts/artifact_cache.py` dans `pipeline_state.json`) sautant instantanément pistes É1, paliers É2 (`V1..V4`) et coutures É3 inchangés ; dispatcher local `scripts/multiscale_autofix.py` exécuté avant toute escalade LLM.

---

## Phase 7 — Psychoacoustique (`P3`) & Mémoire Épisodique de Goût (`P4`)

* **3 Audits Psychoacoustiques `P3` (`deep_diagnostics.py`, `render_and_audit.py`)** :
  * **Masquage dynamique Bark (`analyze_dynamic_bark_masking`)** : mesure `SMR(t, b*)` sur les trames actives du `Lead` (> `-35 dBFS`) ; si `SMR < +1.5 dB` sur `> 25 %` des trames actives, déclenche `APPLY_DYNAMIC_MIRROR_EQ(freq_hz)` (`-3.5 dB`, `Q=1.8` sur `Chords/Pad` ou `BUS Music`).

  * **Plage Dynamique `LRA` (`compute_ebu_r128_stats` / `compute_lra_ebu`)** : contrôle EBU R128/Tech 3342 mono-passe (`edm/trap >= 2.5 LU`, `pop/rock/lofi >= 3.5 LU`, `jazz/classical >= 5.0 LU`) ; sous le seuil, `RELAX_BUS_COMPRESSION` relève `Threshold` `BUS Music` de `+2.5 dB` et ajuste `Gain` limiteur (`-1.5 dB`).
  * **Traduction Sub-Basse petits haut-parleurs (`analyze_bass_harmonic_translation`)** : sur basse `f0 < 65 Hz`, vérifie `R_trans = 10 log10(E[1.7 f0..3.4 f0] / E[0.8 f0..1.25 f0]) >= -14.0 dB` ; sinon `BOOST_BASS_HARMONICS(f0_hz)` active `gl.dist` Soft + boost `gl.eq +2.5 dB @ 2.5 f0`.

---

## Phase 8 — Équilibre Inter-Pistes, Espace 3D & Références Externes (`Q1..Q7`)

* **Auto-gain staging É1 (`gain_staging_lib.py`)** : `measure_active_loudness()` (K-weighting BS.1770, trames `50 ms/hop 25 ms`, gates `-45 dBFS` + `-20 dB` relatif), 9 rôles canoniques, `ROLE_TARGET_OFFSETS_DB[mix_profile]` (ancre `kick` ou `lead`), peak guard `-2.0 dBFS`, `recommended_volume_auto` et `renders/gain_staging_report.json`.
* **Matrice multi-stems bornée (`analyze_stem_hierarchy` v2)** : `lead_vs_harmony` `[3.0, 9.5]`, `bass_vs_kick` `[-3.5,+2.0]`, `snare_vs_kick` `[-4.5,+1.5]`, `hihat_vs_kick` `[-14,-5.5]`, `counter_vs_lead` `[-12,-3]`, `vocals_vs_lead` `[+1,+6]` dB ; toute déviation émet `ADJUST_TRACK_FADER` (`±4 dB/passe`, cumul `8 dB`). `audit_section_track_audibility()` ajoute les planchers `section_lufs -13/-18/-22 dB` (`TRACK_UNDER_AUDIBLE_FLOOR`).
* **Audit stéréo (`run_deep_audit`)** : `stereo_balance_db = |LUFS_L - LUFS_R| <= 1.2 dB` (`FIX_STEREO_BALANCE`) et `mid_high_side_to_mid_db` sur `500 Hz-8 kHz` (`[-14,-1.5]` rythmique, `[-18,-1.5]` jazz/classique) → `WIDEN_HARMONY_OR_PERC` / `NARROW_EXCESSIVE_SIDE`.
* **Références externes (`bootstrap_golden_refs.py --external-dir`)** : `golden_references/external_masters/<profil>/` (2–3 masters, audio **jamais versionné**), empreintes `external_pro_reference` (`bark24`, pente rose, LUFS, LRA, crest, largeur, ratios de bandes) ; `taste_memory.record_validated_taste()` refuse `PQ < 7.8` sauf `--approve-taste` ; fusion `target_fingerprint` plafonnée à `30 %` (ou `60 %` approuvé) de mémoire de goût.
* **Mémoire de Goût (`scripts/taste_memory.py` → `memory/user_taste_profile.json`)** :
  * `record_validated_taste()` mémorise automatiquement tout morceau `PASS` (hors dossiers temporaires, ou explicitement via `--approve-taste` poids `2.0`, max `50` entrées dédupliquées par `project_id`).
  * Si `reference_tracks` est vide au départ en É0, `target_fingerprint.py` interroge `query_taste_prior()` pondéré par distance euclidienne sur `(V, T, E, D)` **avant** le repli générique et inscrit `"source": "user_taste_memory"` dans `multi_scale_contract.json`.

---

## Phase 9 — v4.0 Pont Strudel headless (le même Quality Gate, en entrée compacte)

Le frontend `composition.strudel` (économie de tokens LLM : ~300–600 tokens/ morceau) est évalué en Node.js
headless (`scripts/strudel_eval.mjs`) puis compilé par `scripts/strudel_to_score_ir.py` en `score_ir.json` +
`arrangement_plan.json` **avec les mêmes garde-fous 0 token** (registre 808 `[31,48]`, apex Lead `<= 74`,
voice leading + LIL, b2, clamp frontière 20 ms, `FullCoverageCoreValidator`). Le rendu final passe ensuite
par **ce même quality gate** (`render_and_audit.py --auto-fix`, matrice stems, corridors spectraux) : aucune
règle de mix n'est affaiblie par le pont Strudel. Doc : [03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md](03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md).

---

## Phase 10 — Playbook des Pièges Vérifiés, Journal PQ & RETEX Sessions (v3.0–v4.2)

### 10.1 Playbook des pièges DSP & mixage vérifiés
* **Erreur d'octave `root_note`** = varispeed ±12 st → chipmunk/smudge : verrouiller `root_note == midi_root` (`blueprint_safety.py`).
* **Cloche à partiels inharmoniques (2,76×)** → YIN verrouille le partiel : HPS + anti-partial-lock protégé (`audio_metrics.py`).
* **Accord/dyade joué chromatiquement** → `poly_chord_or_dyad` non jouable (`playable_chromatically == False`).
* **LPF brique < 6.5 kHz à la source + shelf batterie** → « mix en U » : planchers `6.5 kHz` (harmonie) / `9 kHz` (lead) + high-shelf doux (`config/mix_profiles.json`, Ceiling `-1.2 dBTP`).
* **Notches EQ empilés / creusage fondamentale** : `resonances_surgical_eq` (`render_and_audit.py`) limité à `freq_hz >= 800 Hz`, profondeur plafonnée à `-4.0 dB` (évite de creuser la fondamentale à `315 Hz` et de transformer un lead en bourdonnement électrique) ; `low_mids_clean` plafonné à `-1.5 dB` ; budget `2/piste`, `3` total puis `SWAP_SAMPLE`.
* **Stéréo `BUS Music` hors phase** : `gl.stereo` calibré à `Width=1.15, Side=1.5` (`studio_lib.py` ; l'ancien `Width=2.0 / Side=+12 dB` provoquait une anti-phase `-0.34`).
* **Boucle 140 BPM dans projet 142 BPM** → flam dès 2 mesures : verrou BPM `0.05` + stretch exact (`sample_prep_lib.py`).
* **Vibrato ±12 ct lu comme désaccordé sur le master** → mesurer la justesse sur stems fondation `<= 6 ct`.
* **Couplage ISP `gl.limiter` ↔ `LRA` en `trap_rap`** → à `Gain >= 12 dB`, `gl.limiter` peut laisser passer `~+1.3..+1.5 dB` de pics inter-échantillons au-dessus de `Ceiling` ; inversement, écraser `Ceiling` sous `-2.75 dB` avec un compresseur `Master` agressif effondre `LRA < 2.5 LU`. Recette vérifiée : écrêter proprement les transitoires sur `BUS Drums (17)` (`gl.comp` `Ratio ~ 2.6, Attack 8 ms`), garder `Master gl.comp` léger (`Ratio <= 1.5, Threshold ~ -11.5 dB`) et préserver le contraste `Chorus` vs `Bridge`/`Intro`.
* **Chemins personnels absolus en dur** → test `tests/test_docs_hygiene.py` bloquant (0 occurrence de tout chemin personnel).

### 10.2 Journal des morceaux validés & benchmarks
* **Références historiques PQ** : `rosh2` PQ `7.96`, `song1` PQ `8.05` ; régression `song3/4/5` PQ `7.28–7.47` corrigée en v3.0 (cible `PQ >= 7.8` sur les 7 profils de `tests/regression_multi_genre.py`).
* **Benchmark v4.2 (`projects/rap_2min`, 68 mes., 136 BPM, 120.8 s)** : **8/8 pistes = 100 % vrais samples** de `samples/` (`headroom_piano_c4.wav`, `glass_pad_c5_oct60.wav`, `808_5_sat_alt_oct36.wav`, guitare/pluck, batterie réelle), `musical_safety = 0 erreur`, `timbre_coherence = 0.907`, `peak = -1.41 dBFS`, `corr = +0.682`, `harsh_2_5k < 3 %`.

### 10.3 RETEX sessions réelles (bugs corrigés + tests de régression `tests/test_session_bugs.py`)
| Fichier | Bug historique | Correctif & garde-fou CI |
| :--- | :--- | :--- |
| `scripts/compile_song.py` & `target_fingerprint.py` | `%` non échappé dans `help=` argparse (`ValueError` py3.14) | « 100 pourcent » ; test statique anti-`%` sur tous les `help=` |
| `mcp-servers/audio-analyzer/deep_diagnostics.py` | `issues` / `L`,`R` indéfinis sur métrique hors seuil | Déclaration en tête de `run_deep_audit` ; test WAV PLR faible |
| `scripts/render_and_audit.py` | `_tp_limit` absent dans `_apply_autofix_mutations` | Recalcul via `limiter_invariants()` + `get_invariant()` |
| `scripts/strudel_to_score_ir.py` | `--seed 42` écrasait `seed` du `@meta` | `seed` = `meta.seed` ou SHA-256 du source |
| `scripts/song_builder.py` & `prod_server.py` | `preset="Velvet Pad"` et `vibrato_delay_ms` transmis à `gl.sampler` | `spec.preset = ""`, purge `plugin_params` synthé lors du passage en sample |
| `scripts/gain_staging_lib.py` & `math_mix_solver.py` | `"arp"` classé `"lead"` (`-1 dB`) ou `"fx"` au lieu de `"counter_melody"` (`-9 dB`), faisant hurler `Arp Z1/Z2` | Reclassement `"arp"`/`"arpeggio"` → `"counter_melody"` / `"counter"` |
| `scripts/sample_fit_guard.py` & `prod_server.py` | Cloches FX `inharmonic_bell_or_fx` (`Tubular Bell 2` `-237 ct`, `Bell 2`) sélectionnées sur `arp`/`lead` | `inharmonic_bell_or_fx` interdit sur `arp`/`counter`, rétrogradé (`pref=4`, `bell_bonus=0`) dans `_tonal_rank` |
| `scripts/song_builder.py` | Overrides `cutoff_hz`/`attack`/`release` ignorés si `ch.preset` défini (`gl.analog` bloqué à `3.2 kHz LP12` électrique) ; `fine_tune=0` en multizone | Application des overrides de sculpture + `LP 24`, `Chorus=0`, `Reverb=0` sous `2.2 kHz` même avec `preset` ; préservation de `tuning_ct`/`ch.fine_tune` en multizone `Z1/Z2` |
| `scripts/transitions_lib.py` & `song_builder.py` | `Riser Cutoff` sur `mixer=21/22` ciblant `slot=1` (`gl.delay`/`gl.reverb`) au lieu de `gl.eq` (`slot=2`), et insert sans `gl.eq` en automation `Band` | Ciblage `slot=2` sur bus `21/22` et ajout auto de `gl.eq` sur les inserts ciblés par automation EQ |

### 10.4 Formules & réglages opérationnels utiles
* **Durée visée ~3 min** : `bars = round(180 * bpm / 240)` (90 BPM → 68 mes. ≈ 3 min 02 s ; 136 BPM → 68 mes. = 2 min 00 s).
* **Modèles Audiobox/CLAP absents** : `render_and_audit.py --no-neural` (fallback auto).
* **Prune WAV master** : `AI_STUDIO_PRUNE_MASTER_WAV=1` ne conserve que `master.mp3` + le rapport (un WAV 24-bit 3 min pèse ~48 Mo).
* **Blueprint minimal valide** : généré automatiquement par `prod_server._ensure_blueprint` (valide contre `schemas/song_blueprint.schema.json` : `minItems: 1` sur `tracks`, `structure`, `chord_progression`, `motif_cell` complet).

### 10.5 Mixage dynamique & spatial v2, arcs structurels et groove statistique (spec v2.0 « Copier et Faire Mieux »)
* **Sidechain ducking dynamique Kick→Basse calé au tempo** (`math_mix_solver.py`, bloc `dynamic_processing`) : `g_duck(t) = 1 - d_max · env_kick(t)`, attaque `2 ms`, release = double-croche `60/(4·BPM)` s, profondeur `d_max` calibrée par genre dans `[0.25, 0.55]` (Trap/EDM marqué, Lo-Fi/Jazz subtil) — plus de creusement EQ statique du grave.
* **Équilibrage stéréo Mid/Side par bande** : mono strict `< 140 Hz` (`W_low = 0`), médium `W_mid ∈ [0.7, 1.0]`, aigus `W_high ∈ [1.05, 1.30]` (`apply_mid_side_bands`, rapport `buses.mid_side_bands`) — compatibilité mono garantie (corrélation de phase > 0.95 sous 140 Hz).
* **Façonnage de transitoires** (`transient_shape`) sur les rôles drums/keys : le facteur de crête de chaque bus est rapproché de la cible `crest_factor_db` du corridor (pics seuls, jamais de compression globale).
* **Arcs structurels de référence** : `structure_arcs` par profil dans `golden_references/spectral_corridors.json` (sections `intro/verse/chorus/drop/bridge/outro`, LUFS relatifs, activation par stem — mesurés sur masters externes ou prior analytique via `bootstrap_golden_refs.build_structure_arcs`) ; comparaison du morceau généré via `macro_structure_audit.audit_energy_arc_vs_reference` / `compare_energy_arc`.
* **Groove statistique (pocket)** : `groove_lib.apply_pocket_humanization` — covariance vélocité-timing (notes fantômes `v<50` retardées `+6..+16 ms`, accents `v>105` avancés `-5..-1 ms`) et caisse claire *laid-back* `+4..+10 ms` sur les temps 2 & 4 en Lo-Fi/Boom-Bap/R&B.
* **Garde-fou harmonique** : `narrative_harmony.enforce_low_interval_limits` — sous C3 (MIDI 48), tout intervalle entre notes simultanées `≥ 7` demi-tons (Low Interval Limit).
* **Synthèse expressive v2** : `synth_render.py` `build_automation` — couplage vélocité→cutoff, respiration macro-structurelle (sinusoïde 8–16 mesures sur pad/lead), release adaptatif à l'intervalle inter-notes.

### 10.6 Sound design & mixage idiomatiques Trap (audit `rap_ombre_134`, P1-6/P2-7/P2-8)
* **Archétypes `trap_rap`** (`config/timbre_archetypes.json`) : pad `gl.analog` cutoff 900–2200 Hz (attaque douce, HPF canal 200 Hz) au lieu de 6500 Hz ; lead cutoff 1800–3500 Hz au lieu de 9000 Hz ; 808 `release_ms` 250–450 + `glide_ms` 45–60.
* **Kick** : `eq_kick_clean` HPF 30 Hz, **0 dB à 50 Hz** (fondamentale jamais coupée ; clamp dur à −3 dB max sur appel explicite), −2 dB à 210 Hz, punch +3 dB à 95 Hz ; ancre de gain kick `0.95` (sommet de la hiérarchie), sidechain BUS Music allégé (0.25).
* **Chords/Pad** : gain canal 0.78, EQ médiums allégée (HPF 200 Hz, jamais −3 dB sur 3 bandes consécutives 300/480/1600 Hz) ; pad élargi (BUS Music `gl.stereo` Width 2.0, mono < 250 Hz, sends hall) ; centre mono strict kick/bass/snare (pan 0).
* **Variation B** (`narrative_harmony.chorus_variation_b`, opérateur par défaut) : 2e moitié du chorus/drop — syncopation en 16ths, vélocité ±15 %, notes d'approche, pour casser la boucle stricte de 4 mesures.
* **Gate de palette bloquant au rendu** : `render_and_audit.py` ajoute `palette_gate_rejected` à `gate_failures` si `score < 0.60`, cohérence `< 0.60` ou `max_bark_collision > 0.65` (échappatoire `ALLOW_WEAK_PALETTE=1`) ; `catalog_missing` résolu via `samples.db` racine (SSOT).
* **Tests de non-régression** : `tests/test_audit_rap_ombre_fixes.py` (audit §6 : pitch des percussions == `root_note`, registre 808 `[26,41]` + fondamentale d'accord, anti-fuite taxonomique `other/`/tokens tonaux, seuils bloquants de palette).
