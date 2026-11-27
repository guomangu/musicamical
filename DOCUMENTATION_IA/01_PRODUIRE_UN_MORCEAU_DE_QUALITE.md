# 01 — Produire un morceau de QUALITÉ (guide IA complet v4.4)

> Guide de référence unique pour l'IA compositrice : concevoir une instrumentale **riche (8–10 pistes), vivante, groovy, avec un Lead musical et aéré**, piloter la façade MCP 3 outils, et garantir des timbres 100 % vrais samples sans artefacts.

---

## Partie 1 — Concevoir une instrumentale captivante (les 5 piliers musicaux)

Pourquoi certaines instrumentales générées sonnaient **pauvres, monotones ou pénibles à écouter** :
1. Seulement 5–6 pistes actives sans contraste entre Couplet et Refrain (zéro `$pad`, zéro `$arp`/`$counter`, zéro `$open_hat`, zéro `transition`).
2. Un `$lead` écrit en croches ininterrompues (`deg("0 3 2 1 ~ ...", 4).slow(2)` = 3,5 à 4,7 notes/s pendant 2 minutes, 3 notes uniques, zéro note tenue) qui **gâche le morceau** et oblige l'auditeur à le couper.
3. Intro et Outro inversés (batterie seule sans accords parce que `$chords` avait `.muteIn("intro,outro")`).
4. Basse 808 et kick réduits à 1 coup plat par mesure.

### Pilier 1 — Architecture riche de 8 à 10 pistes atomiques

Toujours combiner les **5 pistes batterie de `kit(...)`** + **4 à 5 pistes tonales distinctes** (jamais un squelette de 3 lignes) :

| Couche | Pistes `$` | Rôle musical | Sections conseillées |
|---|---|---|---|
| **Batterie (5 pistes via `kit()`)** | `$kick`, `$snare`, `$hihat`, `$open_hat`, `$perc` | Groove 2 mesures syncopé, rolls ponctuels, `$open_hat` au refrain, `$perc` discret | Géré **automatiquement** par `kit()` : silencieux en `intro`/`outro`, `kick` allégé au `bridge`, `open_hat` sur `chorus`/`drop` |
| **Fondation harmonique** | `$chords` (`sectionGrid`) | Accords voicing LIL (cordes organiques, piano feutré, pad sombre — éviter PWM/Saw ouverts) ; `1 symbole = 1 mesure` (omettre `.slow(2)` sauf pour tenir chaque accord 2 mes. avec répétition) | **Toute la chanson** (ouvre l'`intro` et clôt l'`outro` !) |
| **Nappe / profondeur** | `$pad` (`sectionGrid`, gain `0.40–0.48`) | Épaisseur atmosphérique (attaque lente `200–400 ms`, release long `>= 750 ms`, `LP 24 <= 1,2 kHz` en trap sombre, creux `180–450 Hz`) | `.in("intro,chorus,bridge,outro")` (épaissit le refrain sans noyer le couplet) |
| **Mouvement / contre-chant** | `$arp` ou `$counter` (`arp(...)`, gain `0.26–0.34`) | Micro-arpège / écho fantôme synchronisé (`counter_melody`, **-9 dB vs kick**, cloches inharmoniques interdites, 0 clash b2) | `.in("verse,chorus,bridge")` (discret dans `FX Delay`, jamais au volume du Lead) |
| **Grave / 808** | `$bass` (`bassline(..., {mode:"root", glide:45})`) | Basse 808 ou Bass Guitar syncopée (2–3 attaques/mesure, jamais 1 coup plat) | `.in("verse,chorus")` (coupée en `intro`, `bridge`, `outro` pour le drop !) |
| **Hook / Lead** | `$lead` (`deg("... _ ...", 4).slow(4)`) | Phrase lente, chantante, mémorable, avec **notes tenues `_`** et grands silences | `.only("chorus")` ou `.in("chorus,bridge")` (laisser le couplet respirer !) |

### Pilier 2 — L'art du Lead réussi (anti-mitraillette)

En rap, trap, drill, boom bap ou R&B, **un bon Lead est LENT, ESPACÉ et CHANTANT** — il laisse de l'air au beat et au rappeur :

- **Utiliser impérativement `_` (prolongation) ou `@N` dans `deg(...)`** avec **`.slow(4)`** (1 phrase = 4 mesures = 16 temps) :
  - `0 _ _` = note tenue 3 pas (blanche pointée), `2 _` = note tenue 2 pas (blanche), `~` = silence.
- **Densité cible stricte** : **`0.7 à 1.8 notes/seconde`**, `med_ioi >= 1.0 beat`, **au moins 5 hauteurs distinctes**, **`rest_ratio ∈ [0.25, 0.72]`**.
- **Présence ciblée** : `.only("chorus")` ou `.in("chorus,bridge")` — **jamais** un Lead en boucle ininterrompue sur 50 mesures de couplets + refrains !
- **Contre-exemple INTERDIT** (mitraillette monotone — bloqué par `preflight_strudel_gate.py` `lead_not_frantic` et `bottom_up_analyzer` `lead_too_fast_robotic`) :
  - ❌ `$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).in("verse,chorus")` (3,6 notes/s, durées 100 % égales `0.50 beat`, 0 note tenue !).
- **Exemples de BONS motifs Lead** (lents, phrasés, `gf >= 0.65`, `sw >= 0.65`, `rest ~0.35–0.55`, `~1.0 note/s`) :
  - **Trap Nocturne (F minor, 136 BPM)** : `$lead: deg("0 _ _ ~ 3 _ 2 ~ 0 _ ~ ~ 2 _ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4).gain(0.58).only("chorus")`
  - **Trap Soul / Chill (E minor, 124 BPM)** : `$lead: deg("4 _ 3 _ 2 _ ~ ~ 0 _ 2 _ 3 _ 2 _ 0 _ ~ ~ ~ ~ ~ ~", 4).slow(4).gain(0.55).only("chorus")`
  - **Boom Bap Soul (D minor, 90 BPM)** : `$lead: deg("0 _ 2 _ 3 _ 2 _ ~ ~ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4).gain(0.56).in("chorus,bridge")`
  - **Hard Drill (A minor, 144 BPM)** : `$lead: deg("0 _ _ _ ~ ~ 3 _ 2 _ 1 _ 0 _ ~ ~ ~ ~ ~ ~", 4).slow(4).gain(0.58).only("chorus")`

### Pilier 3 — Arc narratif & transitions automatiques

- **Intro (`I4`)** : `$chords` + `$pad` (ou `$arp` doux) seuls — la batterie et la 808 entrent au Couplet 1 (`drop` !).
- **Couplet (`V12` / `V16`)** : batterie groove (`kick`+`snare`+`hihat`+`perc`) + `$bass` + `$chords` + `$arp` (espace vocal propre, pas de `$lead` envahissant).
- **Refrain (`C8`)** : `transition("before:chorus", "drum_fill_and_riser", {style: "snare_roll"})` + entrée de `$pad` + `$open_hat` + `$lead` + `.lift("chorus", {energy: "+1.5dB"})`.
- **Pont (`B4` / `B8`)** : changement de grille harmonique dans `sectionGrid({bridge: "..."})`, `$kick` coupé automatiquement par `kit()`, `$pad` + `$arp` en suspension avant le dernier refrain explosif.
- **Outro (`O4`)** : retour harmonique `$chords` + `$pad`, batterie et 808 coupées, extinction naturelle.
- *(Même si `transition(...)` est omis, le pont v4.4 injecte automatiquement un `drum_fill_and_riser` avant chaque refrain et un `pickup` avant le 1er couplet sur tout morceau >= 3 sections.)*

### Pilier 4 — Basse 808 & Kits vivants

- **Kits 2 mesures embarqués** (`trap_half`, `trap_full`, `boom_bap`, `drill`, `house`, `pop`, `ambient`) :
  - Émettent automatiquement **5 pistes atomiques** (`$kick`, `$snare`, `$hihat`, `$open_hat`, `$perc`) avec un vrai bounce syncopé, des rolls de hi-hat ponctuels (`hats: "rolls"` = croches + triplets de fin de mesure, **jamais** `hh*16` continu), et une présence par section intelligente (`opts.intro = "hats"` ou `opts.full = true` pour surcharger).
  - Le backbeat (`kick`/`snare`) n'est **jamais** détruit par `var` (les variations portent sur `hihat`/`perc`/`open_hat`).
  - `hihat` fermé et `open_hat` sont strictement séparés côté samples (`prod_server.py` interdit tout Open Hat sur `$hihat`).
- **Basse syncopée** : utiliser `_` et `.slow(2)` dans `bassline` pour un vrai rebond sur 2 mesures, ex. `$bass: bassline("<1 _ ~ 1 ~ 1 _ ~>", {mode: "root", glide: 45}).slow(2).in("verse,chorus")`.

### Pilier 5 — Intention précise & variété timbrale

Toujours renseigner `intent="..."` dans `@meta` avec le **genre + ambiance + 3–4 familles d'instruments** :
- Boom Bap / Jazz : `intent="warm soulful boom bap jazz rap rhodes piano acoustic bass"` → Rhodes + Piano + **Bass Guitar** réelle.
- Dark Trap / Minimal (style Huntrill, voir [analyse_de_la_trap_sombre_et_minimale_aux_nappes_synth_tiques.md](analyse_de_la_trap_sombre_et_minimale_aux_nappes_synth_tiques.md)) : `intent="cold dark minimal synth pad drone atmospheric dark trap 808 sparse pluck"` → **808** (`35–60 Hz` + sauts d'octave, renfort `160 Hz`), **cordes/nappe sombre** (`LP 24 <= 1,2 kHz`, attaque lente `200–400 ms`, release `>= 750 ms`, creux vocal `180–450 Hz`), **micro-mélodie fantôme** espacée (`$arp`/`$lead` en `FX Delay`, jamais un thème envahissant), **snare sec** sans reverb.
- Hard Drill : `intent="aggressive hard drill dark 808 slide punchy snare tense synth"` → **808** + Dark Pad + Synth Pluck.
- EDM / Ambient : `intent="energetic club edm bright synth lead pluck"` / `intent="cinematic atmospheric dark pad strings airy bell ambient"`.

---

## Partie 2 — Contrat Strudel v4.4 (syntaxe, primitives & pre-flight)

### 1. En-tête `@meta` — 1 ligne obligatoire en ligne 1

```js
// @meta 140 C:minor trap_rap V=-0.6 T=0.7 E=0.75 D=0.7 form=I4-V12-C8-V12-B4-C8-O4 dur=120s seed=1337 intent="dark melodic trap 808 hypnotic bell atmospheric pad"
```

| Token | Signification | Défaut |
|---|---|---|
| `140` | BPM (2-3 chiffres) | — |
| `C:minor` | tonalité + mode (`major/minor/dorian/phrygian/aeolian/harmonic_minor/...`) | C:minor |
| `trap_rap` | `mix_profile` (`trap_rap`, `pop_rnb`, `edm_club`, `rock_metal`, `lofi_chill`, `jazz_acoustic`, `classical_ambient`) | trap_rap |
| `V=-0.6` | valence [-1, 1] — libre en v4.3+ (transposition par phrase, gap_fill préservé) | 0.0 |
| `T= E= D=` | tension / énergie / profondeur → `emotion_vector` | 0.5 / 0.7 / 0.5 |
| `form=I4-V12-C8-O4` | sections (`I`=intro `V`=verse `P`=pre_chorus `C`=chorus `B`=bridge `D`=drop `O`=outro) | — |
| `dur=120s` | durée cible multi-signatures (`bars = round(dur_s * bpm / (60 * B_bar))`) | — |
| `intent="..."` | intention textuelle → sélection palette CLAP + lexicale | — |
| `seed=1337` / `ts=4/4` | seed déterministe / signature rythmique | SHA-256 / 4/4 |

### 2. Primitives du prélude v4.4 (jamais de notes brutes)

| Primitif | Rôle | Exemple |
|---|---|---|
| `sectionGrid({default: "...", bridge: "..."})` | grille d'accords par section (**1 symbole = 1 mesure**, ne pas ajouter `.slow(2)`) | `$chords: sectionGrid({default: "Cm7 Abmaj7 Fm7 G7", bridge: "Abmaj7 Ebmaj7 Fm7 G7"}).gain(0.58)` |
| `grid("Cm7 Abmaj7 Fm7 G7")` | grille simple (1 symbole = 1 mesure) | `$chords: grid("Cm7 Abmaj7 Fm7 G7").gain(0.62)` |
| `arp("0 _ ~ ~ 2 _ ~ ~", 4)` | **(v4.4)** contre-chant/écho synchronisé sur la grille (`counter_melody` `-9 dB`, cloches inharmoniques interdites, **0 clash b2**) | `$arp: arp("0 _ ~ ~ 2 _ ~ ~", 4).slow(2).gain(0.28).in("verse,chorus,bridge")` |
| `deg("0 _ _ ~ 3 _ 2 ~ ...", 4)` | mélodie en degrés diatoniques ; **`_` prolonge la note précédente**, **`@N` fixe la durée** | `$lead: deg("0 _ _ ~ 3 _ 2 ~ 0 _ ~ ~ 2 _ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4).gain(0.58).only("chorus")` |
| `bassline("<1 _ ~ 1 ~ 1 _ ~>", {mode:"root", glide:45})` | basse snappée sur la grille, registre `[31, 48]` garanti, supporte `_` | `$bass: bassline("<1 _ ~ 1 ~ 1 _ ~>", {mode:"root", glide:45}).slow(2).in("verse,chorus")` |
| `kit("trap_half", {var:2, hats:"rolls"})` | **(v4.4)** émet 5 pistes atomiques (`kick`, `snare`, `hihat`, `open_hat`, `perc`) avec groove 2 mes. & sections auto | ligne seule, sans `$` (`trap_half`, `trap_full`, `boom_bap`, `drill`, `house`, `pop`, `ambient`) |
| `transition("before:chorus", "drum_fill_and_riser", {style:"snare_roll"})` | transition vers `arrangement_plan.transitions[]` | `drum_fill`, `drum_fill_and_riser`, `harmonic_anticipation`, `impact` |
| `.in()` / `.only()` / `.muteIn()` / `.lift()` | présence & dynamique par section | `.lift("chorus", {energy: "+1.5dB"})` |

### 3. Exemple de référence complet (10 pistes atomiques — varier tonalité, BPM, grille et motif !)

```js
// @meta 140 G:phrygian trap_rap V=-0.75 T=0.80 E=0.72 D=0.82 form=I4-V16-C8-V16-B8-C12-O6 dur=120s seed=1407 intent="cold dark minimal synth pad drone atmospheric dark trap 808 sparse pluck"
kit("trap_half", {var: 3, hats: "rolls"})
transition("before:chorus", "drum_fill_and_riser", {style: "snare_roll"})
$chords: sectionGrid({default: "Gm Gm Ebmaj7 Gm", bridge: "Abmaj7 Fm Gm Gm"}).gain(0.58)
$pad: sectionGrid({default: "Gm Gm Ebmaj7 Gm", bridge: "Abmaj7 Fm Gm Gm"}).gain(0.46).in("intro,chorus,bridge,outro")
$arp: arp("0 _ ~ ~ ~ ~ 2 _ ~ ~ ~ ~ 1 _ ~ ~", 4).slow(4).gain(0.28).in("verse,chorus,bridge")
$bass: bassline("<-7 _ _ ~ ~ ~ 0 ~ -7 _ ~ ~ ~ ~ -3 _ -2 _ _ ~ ~ ~ -2 ~ -7 _ ~ ~ 0 _ ~ ~>", {mode: "stepwise", glide: 55}).slow(4).in("verse,chorus")
$lead: deg("0 _ _ ~ ~ ~ 3 _ 2 _ ~ ~ 0 _ -1 _ -2 _ ~ ~ ~ ~ -1 _ 2 _ 0 _ -1 _ ~ ~", 4).slow(4).gain(0.38).only("chorus")
```

### 4. Pre-flight du MIDI Gate — 0 rendu, ~2 s

```bash
node scripts/strudel_lint.mjs <projet>/composition.strudel        # lint (<= 2 lignes ERR)
.venv/bin/python scripts/strudel_to_score_ir.py <projet> --eval   # pont + rapport preflight
.venv/bin/python scripts/preflight_strudel_gate.py <projet>       # gate simulé complet
.venv/bin/python scripts/preflight_strudel_gate.py --degrees C:minor
.venv/bin/python scripts/preflight_strudel_gate.py --safe-degrees "Cm7 Abmaj7 Ebmaj7 Bb7" --mode minor
```

Seuils du gate : `stepwise_ratio ≥ 0.65` · `gap_fill ≥ 0.60` · `rest_ratio ∈ [0.20, 0.72]` · `contrary_or_oblique ≥ 0.55` · **`lead_not_frantic = PASS`** (détecte les leads mitraillette `> 2.8 notes/s` uniformes).

### 5. Tables de conception (degrés, `.slow(N)`, b2-safe)

**Degrés en mineur** (`SCALES.minor = [0,2,3,5,7,8,10]`, tonic C4 = 60) : `0=C(+0)`, `1=D(+2)`, `2=Eb(+3)`, `3=F(+5)`, **`4=G(+7)`**, `5=Ab(+8)`, `6=Bb(+10)`, `7=C'(+12)`. Apex max : `LEAD_APEX_MAX = 74` (`72` si `V < -0.2`).

**`.slow(N)`** (`step_beats = 4 * N / L`, où `L` = somme des pas incluant `_` et `~`) :
- Phrase Lead lente sur 4 mesures : `L = 16` ou `24` avec **`.slow(4)`** + notes tenues `_` → notes de `1.0 à 3.0 beats` (`0.8–1.5 notes/s`).
- Arpège / basse sur 2 mesures : `L = 8` avec **`.slow(2)`** → pas de `1.0 beat` (noire) ou `0.5 beat` avec silences.
- **Intersection b2-safe sur i–VI–III–VII mineur** : **`{0, 3, 6, 7}`** (zéro demi-ton sur les 4 accords ; `fix_lead_b2_clashes` conserve les appoggiatures résolues sur temps faible et préserve le contour/gap_fill).

---

## Partie 3 — Façade MCP `ai-studio-prod` (3 outils, < 1 800 tokens)

```python
studio_prepare(project_dir: str, intent: str = "", refs: list[str] | None = None,
               remix: str | None = None, lyrics: dict | None = None) -> str          # <= 450 cars

studio_compose(project_dir: str, strudel_code: str, palette_id: str = "auto",
               intent: str = "", palette_strategy: str = "semantic",
               auto_master: bool = True, auto_prepare: bool = False,
               render_mode: str = "full",                                            # "preview" = sonde 12 mes.
               remix_source_path: str | None = None, lyrics_spec: dict | None = None) -> str

studio_refine(project_dir: str, delta_json: dict | None = None, strudel_patch: dict | None = None,
              semantic_patch: dict | None = None, export_mp3: bool = True) -> str
```

| Scénario | Quand | Appel |
|---|---|---|
| **A — Création originale** | nouveau morceau | `studio_compose(projet, strudel_code, auto_prepare=true, intent="...")` (1 tour) |
| **B — Samples verrouillés** | dossier `Samples/` fourni | `studio_prepare(projet, intent, refs=[...])` puis `studio_compose` |
| **C — Remix / D — Chant** | audio source ou paroles | `remix_source_path="..."` / `lyrics_spec={...}` |

Retouches rapides (~20–40 tokens) :
- `studio_refine(projet, semantic_patch={"lead": {"oct": -1, "rests": "+15%", "cutoff": 3200}})`
- `studio_refine(projet, strudel_patch={"replace_track": "lead", "code": 'deg("0 _ _ ~ 3 _ 2 ~ ...", 4).slow(4).only("chorus")'})`

---

## Partie 4 — Sons agréables, multi-zone & Bonnes pratiques qualité (0 token)

### A. Sélection 100 % vrais samples, multi-zone & anti-dureté
- **`palette_id="auto"`** (défaut) : interroge `samples.db` (stratifié par catégorie + CLAP + affinité `intent`), exclut loops/risers/swells des rôles one-shot, sépare strictement closed `hihat` et `open_hat`, et applique le **gate d'agréabilité** (`scripts/timbre_pleasantness.py`, `pleasantness_gate` → `PASS / FIX / SWAP`, crest max `19,5 dB` lead/chords, `14,5 dB` pad — SSOT `config/mix_profiles.json`) + `scripts/sample_fit_guard.py` (politique rôle/catégorie, `root_note` exact, dérivés `±12 st`, dédoublonnage multi-pistes, séparation spectrale `chords` vs `pad`).
- **Multi-zone automatique** (`song_builder._expand_multizone_sampler_channels`) : toute piste tonale non-basse à large tessiture (`ambitus > 10 st`) est automatiquement découpée en **2–3 zones de pitch avec préservation des formants (`<= ±3 st/zone`)** sur le même insert mixeur (cache disque), tout en **préservant le `tuning_ct` / `fine_tune` exact** sur chaque sous-canal `Z1/Z2` (jamais écrasé à `0`).
- **Anti-son électrique / artificiel sur `gl.*` & `arp`/`counter`** :
  - `song_builder.py` honore désormais les overrides `cutoff_hz`/`lpf_hz`, `attack_ms`, `release_ms` et force `Filter mode = LP 24`, `Chorus = 0`, `Reverb = 0` sur `gl.analog` lorsque `cutoff <= 2.2 kHz` même quand un `preset` factory (ex. *Velvet Pad*) est chargé — empêchant les oscillateurs Pulse/Saw ouverts à `3.2 kHz LP 12` de sonner électriques.
  - Sur `$chords` / `$pad`, privilégier des samples organiques non électriques (`DBD_HI STR_C_1`, `headroom_piano_c4`, `eaalah_pad_single`) ou des nappes filtrées `LP 24 <= 1,2 kHz` avec creux `180–450 Hz`.
  - `$arp` est strictement classé **`counter_melody`** (`-9 dB` dans `gain_staging_lib.py` et `"counter"` dans `math_mix_solver.py`, jamais `"lead"`) et `inharmonic_bell_or_fx` (`Tubular Bell 2`, `Steel Stab`) est bloqué sur `arp`/`counter` (`sample_fit_guard.py` + `prod_server.py`).
- **Quality Gate v2** (`sample_quality_v2.py` + `indexer.reroute_sample_path`) : exclut clipping TP×4, faux WAV upsamplés, vrai bruit HF et chemins absents ; `root_note` percussions = hauteur notée du kit (`kick=36`, `snare=38`, `hihat=42`, `open_hat=46`, `perc=39`).
- **Hygiène banque** : `scripts/organize_samples.py --move` puis `python scripts/index_samples.py` (préserve les embeddings CLAP).

### B. Boucle d'analyse ascendante 4 niveaux (`bottom_up_analyzer.py`, `BU=PASS(4/4)`)
1. **Niveau 1 — Sons** : vérifie chaque sample (`playable_chromatically`, `root_note`, `S_max`, `pleasantness_gate` jamais `SWAP`).
2. **Niveau 2 — Accords** : audite chaque accord (LIL, zéro cluster `b2`, voice-leading Viterbi, centrage par piste / multi-zone).
3. **Niveau 3 — Pistes** : anti-`b2` Lead/accord, apex `<= 74`, registre basse `[31..48]`, overlaps `10 ms`, **détection `lead_too_fast_robotic` et `lead_too_narrow`**.
4. **Niveau 4 — Morceau** : sections non vides, pistes actives synchronisées (zéro piste orpheline), plafond cognitif (`<= 10` pistes atomiques / `<= 5` couches tonales), cohérence timbrale Bark24, puis audit audio `master.wav`.

### C. Mixage & fiabilité opérationnelle
- **`math_mix_solver.py`** (1 passe < 1 s, propriétaire unique des faders/EQ/pan/sends) + calibrage nominal `NOMINAL_APPLIED` (`gain_staging_lib.py`) + master doux (`ceiling -1.2 dBTP`, LPF sources `>= 6.5 kHz` harmonie / `9 kHz` lead — SSOT `config/mix_profiles.json`). Ne jamais toucher aux faders à la main.
- **Équilibre `Chords` / `Pad` / dynamique** : creuser le pocket vocal `180–450 Hz` (`-3..-4 dB`), lisser d'abord les pics batterie sur `BUS Drums` avant le `Master`, et conserver du contraste entre couplets/pont (`-2..-3 dB`) et refrains pour préserver `LRA >= 2.5 LU` sans dépassement True Peak.
- **Guides stylistiques spécialisés** : [analyse_de_la_trap_sombre_et_minimale_aux_nappes_synth_tiques.md](analyse_de_la_trap_sombre_et_minimale_aux_nappes_synth_tiques.md) (trap sombre phrygienne, nappes filtrées `LP 24`, micro-mélodies fantômes, snare sec).
- **Vérifier dans `handoff.txt`** : `palette=… samples=N/N`, `safety=0err`, `BU=PASS(4/4)`, `preflight=PASS` (et `lead_not_frantic=PASS`).
- **Worker MCP éphémère** (`prod_worker.py`) : plus besoin de redémarrer après `git pull` (`RESTART session agent` uniquement si la signature des 3 outils change).
- Toujours ranger les projets dans `projects/<nom>`, ne jamais éditer `arrangement_plan.json`/`score_ir.json` à la main.
- **Diagnostic d'un master « boomy / inaudible »** (audit v4.3, détail dans `docs/04` §3.2b) : lire `renders/autofix_history.json` — si `low_end_share > 42 %` (sub+bass < 250 Hz) avec `high_mids`/`air` faibles, le grave masque tout (inaudible sur laptop) : l'autofix corrige désormais le BUS Bass (trim proportionnel + HPF 160 Hz sur BUS Music + notchs sub < 200 Hz sur BUS Bass, règles 6b/3b). Vérifier aussi : `true_peak` (look-ahead limiteur calibré 5 ms, fallback de Gain), résonances 63/80 Hz, vélocités batterie humanisées (hats <= 96, lead >= 72), arc d'énergie des sections non plat, `cutoff_hz` du lead >= 9 kHz (floor SSOT), noms de canaux uniques (`OpenHat` vs `HiHat`), master comp dans les invariants (Threshold >= -12 dB, Makeup <= 0.8 dB).
