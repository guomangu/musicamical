# 01 — Produire un morceau de QUALITÉ (guide IA complet, tout en un)

> Fusion des 3 anciens documents IA (composer avec Strudel v4.1 · produire le morceau via la façade
> 3 outils · sons agréables) + partie 4 : possibilités et bonnes pratiques pour un son de qualité.

---

## Partie 1 — Composer avec Strudel (contrat v4.1)

Le fichier `<projet>/composition.strudel` est la **seule entrée composition** (l'autre entrée possible : `arrangement_plan.json` → même `ScoreIR`). Tout passe ensuite par le pont headless (Node pur `@strudel/core` + `queryArc`, déterministe par `seed`) → `ScoreIR` → garde-fous 0 token → `score_ir.json` + `arrangement_plan.json`.

## 1. En-tête `@meta` — exactement 1 ligne, toujours en première ligne

```
// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4 seed=1337 ts=4/4
```

| Token | Signification | Défaut |
|---|---|---|
| `140` | BPM (2-3 chiffres) | — |
| `C:minor` | tonalité + mode (`major/minor/dorian/phrygian/aeolian/...`) | C:minor |
| `trap_rap` | `mix_profile` (7 profils : trap_rap, pop_rnb, edm_club, rock_metal, lofi_chill, jazz_acoustic, classical_ambient) | trap_rap |
| `V=-0.6` | valence [-1, 1] — **garder V=0.0** (V < -0.2 plie le lead par octaves et casse le gap_fill) | 0.0 |
| `form=I4-V8-C8-O4` | sections : I=intro V=verse P=pre_chorus C=chorus B=bridge D=drop O=outro, `L4` = 4 mesures | — |
| `seed=1337` | seed déterministe | SHA-256 du source |
| `ts=3/4` | signature | 4/4 |

**Durée visée ~3 min** : `bars = round(180 * bpm / 240)` (90 BPM → 68 mesures ≈ 3 min 02 s).

## 2. Primitives du prélude v4.1 (jamais de notes brutes)

| Primitif | Rôle | Exemple |
|---|---|---|
| `grid("Cm7 Abmaj7 Fm7 G7")` | grille d'accords, **1 symbole = 1 mesure** | `$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7).muteIn("intro,outro")` |
| `deg("0 ~ [2 3] 4", 4)` | degrés dans la gamme du `@meta` (octave 4 = C4=60) | `$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).in("verse,chorus")` |
| `bassline("<1 ~ 1 ~>", {mode:"root", glide:40})` | basse en degrés, **snappée sur la grille**, registre `[31,48]` garanti à la source | `$bass: bassline("<1 ~ 1 ~>", {mode:"root", glide:40}).slow(4)` |
| `kit("trap_half", {var:2, hats:"rolls"})` | émet `$kick/$snare/$hihat/$perc` séparés (kits : trap_half, trap_full, boom_bap, house, drill, ambient) | ligne seule, sans `$` |
| `.in("verse,chorus")` / `.only("chorus")` / `.muteIn("intro,outro")` | présence par section (macro-forme) | sur n'importe quelle piste |
| `.slow(N)` | ralentit ×N (voir table §5) | `.slow(2)`, `.slow(4)` |
| `track(role, pat)`, `glide(ms)` | piste nommée / legato | rarement nécessaire |

## 3. Exemple canonique — recopier tel quel (testé en CI : 0 correction de garde-fou **et** pre-flight gate PASS)

```js
// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4
kit("trap_half", {var: 2, hats: "rolls"})
$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7).muteIn("intro,outro")
$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,chorus,outro")
$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.6).sometimesBy(0.25, x => x.degradeBy(0.5)).in("verse,chorus")
```

## 4. Pre-flight du MIDI Gate — 0 rendu, ~2 s, AVANT tout rendu coûteux

```bash
.venv/bin/python scripts/strudel_to_score_ir.py <projet> --eval          # pont + rapport preflight intégré
.venv/bin/python scripts/preflight_strudel_gate.py <projet>               # simulateur du gate
.venv/bin/python scripts/preflight_strudel_gate.py --degrees C:minor      # table degrés↔demi-tons
.venv/bin/python scripts/preflight_strudel_gate.py --safe-degrees "Cm7 Abmaj7 Ebmaj7 Bb7" --mode minor
```

Seuils du gate (mesurés sur la **topline Lead concaténée de toutes les sections** — les frontières inter-sections comptent) :
`stepwise_ratio ≥ 0.65` · `gap_fill ≥ 0.60` · `rest_ratio ∈ [0.20, 0.50]` · `contrary_or_oblique ≥ 0.55`.
Le rapport liste `bad_leaps` (leap + `start_beat`) et `b2_fixed_by_bridge` (combien de notes `fix_lead_b2_clashes` a réécrit — le piège n°1).

## 5. Tables de conception (C mineur)

**Degrés ↔ demi-tons** (`midi = tonic + 12*(deg//7) + SCALES[deg%7]`, SCALES.minor = [0,2,3,5,7,8,10], tonic C4 = 60) :

| deg | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| st | 0 | +2 | +3 | +5 | **+7** | +8 | +10 | +12 | +14 |
| note | C | D | Eb | F | G | Ab | Bb | C' | D' |

**Piège** : deg 4 = **+7 st** (G), pas +4. **Apex** : `LEAD_APEX_MAX = 74`, clamp `while p>74: p-=12` → règle **`base + motif_max ≤ deg 8`** (deg 11 = 79 → clamp −12 → gap_fill détruit).

**`slow(N)`** (1 cycle = 1 mesure = 4 beats ; `degrés/mesure = L/N`, `step = 4/(L/N)` beats) :

| seq L | modificateur | degrés/mesure | step (beats) |
|---|---|---:|---:|
| 8 | aucun | 8 | 0.5 |
| 128 | `.slow(8)` | 16 | 0.25 |
| 256 | aucun | 256 | 0.0156 (1/64 mes.) — **dangereux** |

Règle : 16/mesure → seq 128 + `.slow(8)` ; 8/mesure → seq 64 + `.slow(8)` ou seq 8 sans slow.

**b2-safe par accord** (i-VI-III-VII en C mineur ; un degré est « unsafe » si sa pc est à 1 st d'une pc de l'accord — `fix_lead_b2_clashes` réécrit ces notes et casse le leap+fill) :

| Accord | degrés interdits | degrés sûrs |
|---|---|---|
| Cm7 | 1 (D), 5 (Ab) | 0,2,3,4,6,7 |
| Abmaj7 | 1, 4 (G), 5 | 0,2,3,6,7 |
| Ebmaj7 | 1, 2 (Eb), 5 | 0,3,4,6,7 |
| Bb7 | 2, 4 | 0,1,3,5,6,7 |

**Intersection sûre partout : {0, 3, 6, 7}** = C, F, Bb, C' (+0, +5, +10, +12 st). Sur i-VI-III-VII, un lead 100 % b2-safe ne peut pas avoir gap_fill = 1.0 sur les 4 accords ; sinon adapter le motif par accord.

## 6. Recettes qui passent du premier coup

- **Anti-clone** (`clone_8bar`) : variations seedées `.sometimesBy(0.2-0.3, x => x.degradeBy(0.5))` sur chaque piste rythmique/mélodique ; cycles bass/lead de **16 mesures** (`.slow(4)` / `.slow(2)`), jamais 4 mesures statiques.
- **Leap+fill** : tout saut |d| ≥ 5 st suivi d'un mouvement **contraire de 1 à 3 st** (`d*next < 0`, `|next| ≤ 3`). Motif éprouvé (16 pas, 2 rests) : `0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~` → diffs +5, −2, … (gf=1.0). Chaque motif commence et finit sur le **même degré** (frontières de sections propres).
- **Rests** : ~2 `~` sur 16 éléments, durées < step → rest_ratio ~0.23–0.42 ; pas de `.legato()` (fait tomber rest_ratio sous 0.20).
- **Basse en sens contraire du lead** (contrary_or_oblique ≥ 0.55).
- Ne **jamais** éditer `arrangement_plan.json` à la main : il est régénéré à chaque `--eval`.

## 7. Pièges déjà rencontrés

| Piège | Symptôme | Fix |
|---|---|---|
| `V < -0.2` dans `@meta` | lead plié par octaves, leaps ±12 | `V=0.0` |
| `sometimesBy(x>0, degradeBy)` sur le lead mélodique | clashes b2 au gate final | garder sur batterie/hats seulement |
| seq longue sans `.slow()` | notes de 1/64 de mesure | `.slow(8)` |
| base montante deg 0→4 | clamp −12, gap_fill 0.2–0.4 | base ≤ 1, motif ≤ deg 7 |
| motif conçu sans penser à `fix_lead_b2_clashes` | ~500 notes réécrites, gap_fill détruit | pre-flight + degrés b2-safe (§5) |
| éditer le plan JSON à la main | écrasé au prochain `--eval` | éditer `composition.strudel` |

---

## Partie 2 — Produire le morceau (façade MCP `ai-studio-prod`, 3 outils)

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
- Si `gate=FAIL` : voir [01 §4](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md) — corriger le motif Strudel, vérifier en 0 rendu avec `preflight_strudel_gate.py`, re-composer. **Ne jamais corriger le mix en touchant aux faders** (voir ci-dessous).

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

À l'indexation (`index_samples.py`), chaque sample reçoit un profil v2 (`sample_quality_v2`) : true peak ×4, faux WAV (upsamplé lossy 11/16 kHz), SNR, DC → `REJECTED` exclu de la sélection ; morphologie : signature 808 (pitch drop), T30, saturation, rôle, pitch MIDI, bark24 ; la sélection est hybride SQL + vecteur avec pénalité de masquage Bark24 ; `audit_project_sources.py` rapporte l'alignement de phase Kick/808 (`PHASE_KICK_BASS`). Détails : [docs/08](../docs/03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md).

## Commandes utiles en 0 rendu

```bash
node scripts/strudel_lint.mjs <projet>/composition.strudel        # lint (≤ 2 lignes ERR)
.venv/bin/python scripts/preflight_strudel_gate.py <projet>        # gate MIDI simulé (PASS/FAIL + bad_leaps)
.venv/bin/python scripts/sample_quality_v2.py <fichier.wav> --selftest
```

---

## Partie 3 — Sons agréables : diagnostic & solutions câblées

Symptôme rapporté : le morceau est rythmiquement correct mais **désagréable à l'oreille** — les sons
choisis sont durs, grinçants ou se masquent entre eux.

## Diagnostic (4 causes racines)

1. **Confort « mou » seul** : `compute_comfort_score` était un score souple — un sample dur/grinçant
   pouvait encore gagner sur le fit de rôle. Aucun **plafond dur** sur la dureté.
2. **Aucune dissonance sensorielle par paire** : deux timbres proches en fréquence critique
   (rugosité Plomp-Levelt) ou qui se chevauchent dans les mêmes bandes de Bark graves se masquent
   (boue dans les bas-médiums) — jamais mesurée dans une palette.
3. **Carte de plugins statique** : la façade `palette_id="auto"` utilisait une table `ROLE_PLUGIN`
   fixe, ignorant le profil de mix et la valence (les archetypes calibrés `gl_dsl.resolve_archetype`
   n'étaient pas appliqués).
4. **La façade ne cherchait pas dans `samples/`** : `palette_id="auto"` ne proposait que des
   synthés `gl.*`, même quand des samples propres étaient indexés ; et les samples posés à la
   racine de `samples/` restaient physiquement non classés.
5. **Serveur MCP périmé après `git pull`** : le processus serveur en mémoire garde l'ancien code
   → `palette: null`, pistes 100 % `gl.*`, aucune ligne `palette=`. Correctif : `studio_compose`
   détecte que `prod_server.py` a changé depuis son démarrage et affiche `RESTART session agent`
   (retour + `handoff.txt`) → fermer/rouvrir la session agent. `install.sh` fait aussi un
   `git pull --ff-only` et rappelle de redémarrer.
6. **Chemins de samples fantômes** (DB indexée depuis une ancienne racine, ex. `.../musicos/...`) :
   `indexer.reroute_sample_path` re-reroute sous `<racine>/samples/...` si le fichier existe, sinon
   le sample est exclu (jamais branché).
7. **`root_note` des percussions absurde** (pitch détecté d'un one-shot, ex. rimshot à 84 → chaque
   hit pitch-shifté de −40 st = inaudible) : désormais `root_note` = **hauteur notée du kit**
   (kick=36, snare=38, hihat=42, lu dans `score_ir.json`, repli GM).
8. **Lookup blueprint par rôle seul au rendu** : les 4 pistes drums partagent le rôle `drums`, donc
   `song_builder._default_channels_for_plan` ne retrouvait jamais la piste `$kick`/`$snare` → le sample
   branché dans le blueprint n'atteignait PAS le canal (rendu en `gl.kick`/`gl.snare` malgré tout).
   Correctif : indexation du blueprint par rôle **et par nom** de piste. Lié : les `plugin_params`
   de confort (`pick_gl_plugin` : cutoff ≤ 9 kHz, attaque ≥ 2 ms…) étaient perdus (seul le nom du
   plugin était stocké) → désormais stockés dans le blueprint et appliqués au canal (`cutoff_hz`).
9. **Volumes inégaux** : sans métriques soundcheck, le gain staging applique un calibrage nominal
   **absolu** par rôle (ancre = kick ; chaque piste réglée à `ancre × 10^(offset/40)`, ex. hihat −10 dB,
   pad −8 dB) au lieu d'un décalage borné à ±6 dB qui ne suffisait pas ; le rapport affiche enfin le
   vrai `volume_before` et le statut `NOMINAL_APPLIED`.
10. **Garde-fou timbral & registre (`scripts/sample_fit_guard.py`, v4.2)** : politique catégorie/nature
    par rôle (FX interdit sur accords, kick interdit en basse, `inharmonic_bell_or_fx` interdit sur
    accords/pad/basse), filtre de transposition `S_max` sur les notes réelles (`score_ir.json`),
    dédoublonnage `perc != hihat`, revalidation automatique après `studio_refine`, purge des anciens
    `renders/master.wav` si aucun rendu frais n'a eu lieu, et observabilité compacte
    (`safety=0err/0warn`, `sound_guard=OK|FIXED`, `rej=K` dans le retour/`handoff.txt`).

**Preuves concrètes sur un morceau généré** (blueprint + handoff fournis) : les 8 pistes étaient
toutes `source_type: "gridline_native"` (`gl.keys`, `gl.fingerbass`, `gl.warmsynth`, `gl.pluck`,
`gl.kick`…) avec **aucune clé `palette`** — la résolution de palette n'avait jamais branché de
sample ; et le log d'indexation montrait `q2_pass: 0, q2_rejected: 0` sur 7 324 rows DSP : la
table `sample_quality_v2` n'était remplie que pour les nouveaux fichiers, jamais en incrémental.

## La solution désormais câblée (0 token pour l'agent)

`scripts/timbre_pleasantness.py` fournit le **gate d'agréabilité** :

- `pleasantness_gate(metrics, role)` → verdict dur **PASS / FIX / SWAP** + score + raisons.
  Plafonds durs par rôle (`PLEASANTNESS_CAPS`) : `r_harsh`, `roughness` (Plomp-Levelt), `peak_harsh_db`,
  plancher `comfort_min` ; plus pour les rôles tonaux : centroïde max (lead 6,5 kHz, chords 5,5 kHz,
  pad 4,5 kHz, basse 900 Hz) et crest max (lead 14 dB, chords 13 dB, pad 12 dB). SWAP si un plafond
  est dépassé, FIX si on en approche (> 80 %), PASS sinon. Les cymbales (hihat) sont tolérées
  (plafonds hauts).
- `plomp_levelt_roughness(freqs, mags)` : rugosité sensorielle d'un spectre (paires de partiels
  dans une bande critique, courbe de dissonance Zwicker).
- `interval_consonance(f1, f2)` : octave > quinte > … > triton (~0,05) — le triton est le pire.
- `palette_timbre_coherence(items)` : collision Bark24 pondérée graves + rugosité par paire de
  centroïdes médiums → score de cohérence de la palette entière.
- `pick_gl_plugin(role, mix_profile, valence)` : plugin `gl.*` agréable via les archetypes
  (`resolve_archetype`, dark si valence < −0,2) avec garde-fous de confort : attaque ≥ 2 ms,
  release ≥ 60 ms, cutoff ≤ 9 kHz sur les rôles tonaux.

**Câblage (tout est automatique) :**

| Où | Effet |
|---|---|
| `palette_builder.propose_palettes` | candidats SWAP filtrés, confort réel des synthés `gl.*`, pénalité de cohérence `−0,2×(1−score)`, champ `timbre_coherence` en sortie |
| `select_sample.py` | les samples SWAP sont exclus de la sélection |
| `prod_server._resolve_palette` (systématique, **tout** `palette_id`) | interroge `samples.db` global + `Samples/`/`samples/` du projet et branche les meilleurs samples RÉELS sur les pistes (`source_type="sample"`, `sample_path`, `gl.sampler`, `root_note`, `fine_tune_ct`) ; **préfère la palette la plus riche en samples réels** (un morceau doit utiliser les sons fournis, pas retomber sur 100 % `gl.*`) ; repli `gl.*` par archetype sinon. Le retour et `handoff.txt` affichent `palette=<id> samples=N theme=…` (observabilité) |
| `prod_server._ensure_blueprint` | plugin `gl.*` par archetype calibré pour **toutes** les pistes (percussions → repli direct `gl.kick`/`gl.snare`/`gl.hat`, jamais un archetype basse) |
| `pick_gl_plugin` | garde-fous de confort **forcés** sur les rôles tonaux : attaque ≥ 2 ms, release ≥ 60 ms, cutoff ≤ 9 kHz même si l'archetype fournit des valeurs plus sèches/aiguës (son criard) |
| `indexer.populate_dsp_metrics` | **backfill v2** : les rows déjà analysées en DSP mais sans profil `sample_quality_v2` sont ré-analysées en incrémental (`q2_pass` réel, exclusion REJECTED effective) |
| `indexer.reroute_sample_path` + `palette_builder` | chemins stale re-rootés, fichiers absents exclus, **`sample_quality_v2` REJECTED exclus dès la proposition de palettes** |
| `sample_quality_v2` | clamp dur `n_fft <= len(mono)` : plus de `UserWarning librosa n_fft too large` |
| `gain_staging_lib.apply_nominal_balance` + `song_builder` | calibrage nominal **absolu** par rôle (volumes égaux sans mesure) + rapport `volume_before`/`NOMINAL_APPLIED` correct |
| `prod_server._ensure_blueprint` | plugin `gl.*` choisi par `pick_gl_plugin` (plus de table statique) |
| `sample_quality_v2` (indexation) | samples REJECTED (clipping TP×4, faux WAV, SNR, DC) exclus d'emblée — voir [docs/08](../docs/03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md) |
| `scripts/organize_samples.py --move` | classe physiquement les samples de la racine de `samples/` dans leur sous-dossier (`808/`, `kick/`, …) sans jamais écraser, puis relancer `index_samples.py` |

## Consignes pour l'IA qui compose

1. **Ne touche pas aux timbres** : laisse `palette_id="auto"` (défaut). La recherche de samples et
   le gate d'agréabilité sont automatiques. Ne force un plugin `gl.*` que via un archetype adapté
   au profil (jamais un son dur pour lead/chords/pad).
2. **Vérifie la ligne `palette=… samples=N`** dans le retour de `studio_compose` et dans
   `handoff.txt` : si `palette=n/a` ou `samples=0` → soit le serveur MCP tourne un vieux code (voir
   `RESTART session agent` → redémarrer la session agent après chaque `git pull`/`install.sh`), soit
   relancer l'indexation
   (`python scripts/index_samples.py` — backfill v2 inclus), ou déposer les samples dans
   `<projet>/Samples/` classés par dossier.
3. Évite les intervalles de **triton** entre lead et basse/accompagnement dans `deg(...)` —
   privilégie {0, 3, 6, 7} (voir [01 §tables b2-safe](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md)).
4. Si tu fournis tes propres samples : mets-les dans `<projet>/Samples/` **classés par dossier**
   (`python scripts/organize_samples.py --move` puis réindexe), et passe par `studio_prepare`
   (scénario B) pour verrouiller la palette.
5. Vérification rapide d'un son en 0 rendu :
   `python scripts/timbre_pleasantness.py --role lead_melody --metrics '{"r_harsh":0.6,"roughness":0.3}'`
   → SWAP = à remplacer ; `--plugin lead trap_rap -0.6` → plugin `gl.*` conseillé.

Règle d'or : **un son SWAP ne doit jamais entrer dans le morceau** — le gate le fait tout seul ;
ne le contourne pas.

---

## Partie 4 — Bonnes pratiques & possibilités pour un son de QUALITÉ (checklist IA)

Tout est câblé en 0 token : l'agent n'a qu'à **vérifier les lignes de retour** et suivre les règles.

### A. Choisir les sons (100 % vrais samples de `samples/`, pas de sons synthétiques artificiels)
- Laisser `palette_id="auto"` (défaut) : la façade privilégie **systématiquement les vrais samples enregistrés** de `samples/` (`headroom_piano_c4.wav`, `rhodes_mk1_d4.wav`, `glass_pad_c5`, guitares/plucks, 808, batterie réelle) avec centrage d'octave polyphase `±12 st` automatique dans `<projet>/derived_samples/`. Les synthés `gl.*` ne servent qu'en dernier recours si aucun sample sain n'existe. Vérifier `palette=… samples=N/N` dans `handoff.txt` (**N/N attendu**).
- Le **gate d'agréabilité** (`scripts/timbre_pleasantness.py`) + **`scripts/sample_fit_guard.py`** filtrent les sons durs (`SWAP`), les stabs métalliques sur les accords, les cris vocaux dans la batterie et les reverse/swells sur `lead`/`arp`, sans faux positifs sur les instruments acoustiques chauds (`peak_harsh_db` évalué uniquement si `r_harsh >= 0.06`).
- Les samples **REJECTED** par le quality gate v2 (clipping TP×4, faux WAV upsamplé, SNR, DC) sont exclus dès la proposition de palettes ; les fichiers absents/chemins stale sont re-rootés (`indexer.reroute_sample_path`).
- Enveloppes `gl.sampler` calibrées par rôle (`song_builder.py` : `voices=8` sur `chords`/`pad`, `voices=1` mono sur `808/bass`, `one_shot=1` batterie, LPF doux 6–9 kHz).
- `root_note` des percussions = **hauteur notée du kit** (`kick=36`, `snare=38`, `hihat=42`, `perc=39`), jamais le pitch détecté d'un one-shot ; `fine_tune_ct=0` sur percussions non-kick.
- Ranger sa bibliothèque : `install.sh` le fait automatiquement (`organize_samples.py --move` avant indexation).

### A-bis. Boucle d'analyse ascendante 4 niveaux (« du plus petit au plus grand », 0 token)
Exécutée automatiquement dans `studio_compose` (`scripts/bottom_up_analyzer.py`, rapport `<projet>/renders/diagnostics/bottom_up_report.json`, résumé `BU=PASS(4/4)` dans `handoff.txt`) :
1. **Niveau 1 — Sons** : vérifie chaque sample choisi de `samples/` par rapport aux accords/notes joués (pureté, politique catégorie/nature, justesse, `S_max`, `pleasantness_gate`) ; dérive `±12 st` ou remplace au besoin.
2. **Niveau 2 — Accords** : audite chaque accord de la grille (Low Interval Limit, zéro cluster `b2`, douceur du voice-leading Viterbi, alignement fondamentale basse, centrage sur la `root_note` du sample) et ré-optimise tout accord fautif.
3. **Niveau 3 — Pistes** : audite chaque piste par rapport à la grille et au morceau (anti-`b2` Lead/accord, apex `<= 74`, registre basse `[31..48]`, silences de respiration, suppression des overlaps 10 ms).
4. **Niveau 4 — Morceau** : audite la cohérence globale (sections non vides, plafond cognitif `<= 8` pistes simultanées, cohérence timbrale Bark24 inter-pistes, puis audio final `master.wav` : peak, RMS, corrélation stéréo, dureté 2–5 kHz).

### B. Harmonie & rythme (passer les gates du premier coup)
- Toujours `// @meta …` 1 ligne + `form=` + présence par section (`.in()/.only()/.muteIn()`), `V=0.0`.
- Jamais de notes brutes : `grid(...)` / `deg(...)` / `bassline(...)` / `kit(...)` uniquement.
- Pre-flight **avant** tout rendu : `python scripts/preflight_strudel_gate.py <projet>` (0 rendu, ~2 s) ;
  viser `preflight=PASS gf≥0.60 sw≥0.65 rest∈[0.20,0.50] bad_leaps=0` ; degrés b2-safe `{0, 3, 6, 7}` (voir Partie 1 §5).
- Anti-clone : variations seedées `.sometimesBy(0.2–0.3, x => x.degradeBy(0.5))` sur les pistes rythmiques,
  cycles bass/lead de 16 mesures.

### C. Égalité des volumes & master propre
- Sans soundcheck, le gain staging applique un calibrage **nominal absolu** par rôle (ancre kick ;
  hihat ≈ −10 dB, pad/chords ≈ −8 dB…) → vérifier `gain_staging_report.json` : statuts `NOMINAL_APPLIED`,
  `volume_before ≠ volume_after` pour les pistes non-ancrées.
- Master : plafond limiteur **−1.2 dBTP** (`config/mix_profiles.json`), jamais de LPF brique < 6.5 kHz (accords)
  / 9 kHz (lead) ; le solveur `math_mix_solver.py` possède les faders (0 rendu), micro-passe scalaire
  `scalar_master_fix` autorisée ; ne jamais toucher aux faders à la main.

### D. Fiabilité opérationnelle
- **Toujours ranger les projets dans `projects/<nom>`** : `prod_server._resolve_project_dir()` verrouille automatiquement tout nom court ou chemin relatif/externe vers `$AI_STUDIO_DIR/projects/<nom>`, et `install.sh` synchronise `~/Music/AI-Studio` vers le dépôt actif.
- **Après chaque `git pull` / `install.sh` : redémarrer la session agent** (les serveurs MCP chargent le code au démarrage). Signes d'un serveur périmé : `palette: null`, pistes 100 % `gl.*`, ou alerte `RESTART session agent` dans le retour/`handoff.txt`.
- `install.sh` fait `git pull --ff-only`, organise les samples, réindexe (backfill q2) : `AI_STUDIO_GIT_PULL=0`
  pour désactiver ; tous les paramètres sont dans `.env` (voir `.env.example`).
- Retouches économes : `studio_refine(semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` (~20–40 tokens)
  plutôt qu'une réécriture de piste.
- Jamais éditer `arrangement_plan.json`/`score_ir.json` à la main : régénérés à chaque compose.
