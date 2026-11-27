# DOCUMENTATION_IA — Runbook Production v4.4 (<= 750 tokens)

Lecture prod (remplace [`docs/`](../docs/00_SOMMAIRE.md)) : **2 documents** — ce `README.md` + [01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md) (8–10 pistes, Lead lent `_`, Strudel, façade 3 outils, qualité timbrale).

## Flux (1 appel `auto_prepare=true`, ou 2 si B/C/D)
1. `studio_prepare(project_dir, intent)` — **Scénarios B/C/D seuls** (`Samples/`, `remix`, `lyrics`). `project_dir="projects/<nom>"`.
2. `studio_compose(project_dir, strudel_code, palette_id="auto", intent="...", auto_master=true, auto_prepare=true)` → Node → `ScoreIR` → **vrais samples** → multi-zone (`<= ±3 st`) → `BU=PASS(4/4)` → master + `handoff.txt`.
3. Retouche : `studio_refine(project_dir, semantic_patch={"lead":{"oct":-1,"rests":"+15%"}})` ou `strudel_patch={"replace_track":"lead","code":"..."}`.

## Contrat `composition.strudel` (8–10 pistes — varier tonalité, grille, BPM, motif !)
- `// @meta 140 C:minor trap_rap V=-0.6 T=0.7 E=0.75 D=0.7 form=I4-V12-C8-V12-B4-C8-O4 [dur=120s] [intent="..."]`
- `kit(...)` émet **5 pistes** (`kick`, `snare`, `hihat` rolls, `open_hat`, `perc`) + **4–5 tonales** (`$chords`, `$pad`, `$arp`, `$bass`, `$lead`) + `transition(...)`.
- **Lead lent & aéré** : `_` (tenue) + `.slow(4)` (`0.7–1.8 notes/s`, silences `35–68 %`, `>= 5` hauteurs) — **JAMAIS** de croches `.slow(2)` en boucle !

```js
// @meta 140 C:minor trap_rap V=-0.6 T=0.7 E=0.75 D=0.7 form=I4-V12-C8-V12-B4-C8-O4 intent="dark melodic trap 808 hypnotic bell pad"
kit("trap_half", {var: 2, hats: "rolls"})
transition("before:chorus", "drum_fill_and_riser", {style: "snare_roll"})
$chords: sectionGrid({default: "Cm7 Abmaj7 Fm7 G7", bridge: "Abmaj7 Ebmaj7 Fm7 G7"}).slow(2).gain(0.68)
$pad: sectionGrid({default: "Cm7 Abmaj7 Fm7 G7", bridge: "Abmaj7 Ebmaj7 Fm7 G7"}).slow(2).gain(0.42).in("intro,chorus,bridge,outro")
$arp: arp("0 ~ 2 ~ 1 ~ 3 ~", 4).slow(2).gain(0.40).in("verse,chorus,bridge")
$bass: bassline("<1 _ ~ 1 ~ 1 _ ~>", {mode: "root", glide: 45}).slow(2).in("verse,chorus")
$lead: deg("0 _ _ ~ 3 _ 2 ~ 0 _ ~ ~ 2 _ 4 _ 3 _ 2 _ 0 _ ~ ~", 4).slow(4).gain(0.58).only("chorus")
```

## 6 règles absolues
1. `8–10 pistes atomiques` (`1 $ = 1 rôle`, 0 note brute) ; `$arp` = `counter_melody` discret (0 cloche inharmonique, jamais niveau Lead) ; intro/outro harmoniques.
2. **Lead anti-mitraillette** : `_` + `.slow(4)`, `0.7–1.8 notes/s`, `rest ∈ [0.20, 0.72]`, `.only("chorus")` ou question-réponse.
3. **Timbres naturels (`samples/` ou `gl.*` sculptés)** : éviter PWM/Saw ouverts (`song_builder` applique `cutoff_hz`/`LP 24`/`Chorus=0` même sur `preset`) ; `BU=PASS(4/4)`.
4. MCP `ai-studio-prod` seul (3 outils, < 1 800 tokens) ; `project_dir="projects/<nom>"`.
5. Pré-flight 0 rendu : `preflight_strudel_gate.py <projet>` (`gf>=0.60`, `sw>=0.65`, `lead_not_frantic=PASS`).
6. Worker MCP éphémère : pas de redémarrage après `git pull`.
