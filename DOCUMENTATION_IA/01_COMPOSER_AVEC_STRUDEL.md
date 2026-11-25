# 01 — Composer avec Strudel (contrat v4.1, compact)

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
