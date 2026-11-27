# Rapport de Diagnostic — Pourquoi `Chords`, `Lead` et les sonorités sont éclatées & Pourquoi les samples tagués dans `samples/` ont été ignorés

**Date** : 27 novembre 2026  
**Projets concernés** :
- `projects/rap_boombap_soul_2min`
- `projects/rap_trap_melodic_dark_2min`
- `projects/rap_drill_hard_2min`

---

## 1. Résumé Exécutif (La vérité brute)

Tu as raison sur toute la ligne : **la quasi-totalité des vrais samples instrumentaux de ton dossier `samples/` (pianos, guitares, pads, synthés que tu as scannés et tagués) ont été bloqués en amont par le pipeline**, ne laissant survivre qu'une poignée de fichiers (~3 pianos/Rhodes).  
Résultat : les trois morceaux (Boom Bap, Dark Trap, Hard Drill) ont tous pioché dans les **mêmes 3 fichiers** (`rhodes_mk1_d4.wav`, `rhodes_tine_lead_f4.wav`, `headroom_piano_c4.wav`) pour `Chords` et `Lead`, joués en varispeed mono-sample dans `gl.sampler`.

---

## 2. Parcours exact de ce qui a été fait pour `Chords` et `Lead`

Voici la chaîne complète exécutée lors de la génération des 3 morceaux via `studio_compose(..., palette_id="auto", auto_prepare=True)` :

1. **Évaluation Strudel (`composition.strudel` → `score_ir.json`)** :
   - Le code Strudel définit `$chords` (`sectionGrid(...)`) et `$lead` (`deg(...)`).
   - `strudel_to_score_ir.py` convertit ces motifs en notes MIDI dans `score_ir.json` et `arrangement_plan.json`.
2. **Sélection des sonorités (`prod_server._resolve_palette` → `scripts/palette_builder.py`)** :
   - La façade appelle `palette_builder.propose_palettes(db_path="mcp-servers/audio-index/samples.db", ...)`.
   - `palette_builder._load_sqlite_candidates()` interroge `samples.db` avec un filtre SQL strict (`playable_chromatically = 1`, `hard_reject = 0`, `q2.status != 'REJECTED'`, `s.category IN (...)`).
   - Pour les 3 morceaux, ce filtre a **éliminé > 93 % des samples tonaux** de `samples/` et n'a renvoyé que quelques survivants, dominés par 3 fichiers dans `samples/piano/` :
     - **Boom Bap** : `Chords` = `samples/piano/rhodes_mk1_d4.wav` | `Lead` = `samples/piano/rhodes_tine_lead_f4.wav`
     - **Dark Trap** : `Chords` = `samples/piano/rhodes_mk1_d4.wav` | `Lead` = `derived_samples/headroom_piano_c4_oct72.wav` (dérivé de `samples/piano/headroom_piano_c4.wav`)
     - **Hard Drill** : `Chords` = `samples/piano/headroom_piano_c4.wav` | `Lead` = `samples/piano/rhodes_tine_lead_f4.wav`
3. **Compilation Gridline (`scripts/compile_song.py` → `song_builder.py`)** :
   - Chaque piste `Chords` et `Lead` est instanciée en `gl.sampler` avec un **unique fichier WAV** (`data = "/.../rhodes_mk1_d4.wav"` etc.), un `root_note` et un `fine_tune_ct` uniques.
   - Toutes les notes d'accords et de mélodie sont donc jouées par **pitch-shift varispeed** à partir d'un seul WAV (sans multi-zones ni vélocité multi-couches), avec un LPF source (`cutoff_hz` entre `5800` et `9000 Hz`).
   - Le gain staging applique ensuite un calibrage nominal (`gain_staging_report.json` : `Chords` à `-3.57 dB`, `Lead` à `-0.04 dB`, `Bass` à `+4.44 dB`).
4. **Rendu (`scripts/render_and_audit.py` + `gridline`)** :
   - Gridline rend `projet.gridline` vers `renders/master.wav` et `renders/master.mp3`.

---

## 3. Les 4 Causes Racines (Pourquoi tes samples tagués n'ont pas été utilisés)

### Cause n°1 (Majeure) : `playable_chromatically = 0` bloque 93,4 % des instruments tonaux dans `samples.db`
Lors de l'inspection directe de `mcp-servers/audio-index/samples.db` (`16 476` entrées, dont `16 476` taguées et `15 801` avec embedding CLAP) :

| Catégorie | Total dans `samples.db` | `playable_chromatically = 0` (Bloqués) | `playable_chromatically = 1` (Utilisables) |
| :--- | :---: | :---: | :---: |
| **`piano`** | 149 | **129 (86,6 %)** | **20** |
| **`synth`** | 338 | **322 (95,3 %)** | **16** |
| **`guitar`** | 181 | **173 (95,6 %)** | **8** |
| **Total tonal** | **668** | **624 (93,4 %)** | **44 (6,6 %)** |

**Pourquoi ?** Dans `scripts/audio_metrics.py` (lignes 655–669) :
```python
is_melodic_seq = (
    ((f0_std_st > 0.75 or frac_off_pitch > 0.14) and not is_low)
    or (onset_count >= 3 and f0_std_st > 0.35)
)
```
Dès qu'un vrai sample de piano, guitare, pad ou synthé possède un vibrato naturel, un chorus/detune, une attaque percussive ou une queue de réverbération où YIN décroche sur plus de `14 %` des frames (`frac_off_pitch > 0.14` ou `P85 > 0.75 st`), il est étiqueté **`melodic_sequence`** (`playable_chromatically = 0`).

**Impact direct sur tes packs dans `samples/`** :
- `Lofi/Piano` : **22 / 22** bloqués en `melodic_sequence` (`0` utilisable)
- `Lofi/Guitar` : **25 / 25** bloqués en `melodic_sequence` (`0` utilisable)
- `Lofi/Pad` : **15 / 16** bloqués (`1` seul utilisable)
- `Sega-93 Pad Sample Pack` : **24 / 28** bloqués (`0` utilisable en piano/synth)
- `synth-samples` : **47 / 50** bloqués (`3` utilisables)

### Cause n°2 : Le goulot d'étranglement SQL dans `palette_builder.py`
Dans `scripts/palette_builder.py` (`_load_sqlite_candidates`, lignes 777–795) :
- Pour `lead_melody` et `chord_keys`, la requête impose `d.playable_chromatically = 1` et `COALESCE(q2.status, 'PASS') != 'REJECTED'`.
- Lors du test réel sur `lead_melody`, sur **16 476 samples**, la requête SQL ne retourne que **21 candidats** (dont **10 sont des `vocal`**, `3` des `piano`, `3` des `synth` ; `3` sont encore exclus par `pleasantness_gate`, laissant **16** candidats).
- De plus, le tri SQL (`ORDER BY (r_harm + playable_chromatically + band_mid - 0.7*r_harsh) DESC`) place systématiquement `rhodes_mk1_d4.wav`, `rhodes_tine_lead_f4.wav` et `headroom_piano_c4.wav` tout en haut de la pile, **quel que soit le style demandé** (Boom Bap, Trap ou Drill).

### Cause n°3 : Désynchronisation massive entre `samples/` sur disque et `samples.db`
- **Sur disque (`samples/`)** : `10 335` fichiers audio réels.
- **Dans `samples.db`** : `16 476` lignes, dont **`7 099` chemins morts** (fichiers déplacés ou renommés par `organize_samples.py` ou absents du disque), et **`2 526` marqués `REJECTED`** dans `sample_quality_v2`.
- L'IA voit une base polluée à ~43 % par des chemins fantômes, et les rares survivants valides pour `Chords`/`Lead` sont toujours les mêmes 3 fichiers de `samples/piano/`.

### Cause n°4 : Rendu `gl.sampler` mono-sample en varispeed
- Même sur les 3 survivants (`rhodes_mk1_d4.wav`, `rhodes_tine_lead_f4.wav`, `headroom_piano_c4.wav`), `gl.sampler` joue **un seul fichier WAV** transposé par varispeed sur toute la grille d'accords et toute la mélodie.
- Dès que les notes s'éloignent de la `root_note`, la durée d'attaque/relâchement et les formants sont étirés/compressés artificiellement, ce qui donne un son plastique et plat, sans aucune texture de synthé Trap/Drill ni richesse harmonique Boom Bap.

---

## 4. Pistes de Correction Recommandées (pour le dev du framework)

1. **Assouplir `is_melodic_seq` dans `scripts/audio_metrics.py`** : ne pas classer un one-shot tonal en `melodic_sequence` sur un simple `frac_off_pitch > 0.14` (mesurer la stabilité sur le *sustain core* hors attaque/queue, et exiger `onset_count >= 2` ou un vrai changement de note discret avant de bloquer un piano/pad/synth/guitare).
2. **Purger et réindexer `mcp-servers/audio-index/samples.db`** : supprimer les `7 099` entrées mortes et ré-évaluer `sample_dsp_metrics` pour débloquer les packs `Lofi/`, `Sega-93`, `synth-samples`, `piano/`, `guitar/`.
3. **Diversifier `palette_builder.py` selon le genre/intent** : empêcher que `r_harm` pur ne fasse gagner les mêmes 3 Rhodes/pianos sur de la Trap ou de la Drill, et privilégier les vrais synthés/bells/pads/guitares tagués dans `samples.db` quand l'intent demande `trap`, `drill`, `synth`, `pad` ou `guitar`.
4. **Ne plus limiter `Chords`/`Lead` à un unique WAV varispeed** quand un synthé natif adapté (`gl.keys`, `gl.warmsynth`, `gl.pluck`) ou un multi-sample SFZ/multi-zone est nécessaire pour garder un timbre propre sur toute l'étendue des accords.
