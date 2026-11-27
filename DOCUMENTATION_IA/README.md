# DOCUMENTATION_IA — Runbook Production v4.3 (2 documents, <= 750 tokens)

Seule lecture pour produire un morceau (remplace les 5 docs dev [`docs/`](../docs/00_SOMMAIRE.md)) : **2 documents** — ce fichier (`README.md`, préfixe 100 % statique) + [01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md) (guide complet : Strudel, façade 3 outils, diagnostic timbres, bonnes pratiques).

## Flux (1 appel `auto_prepare=true`, ou 2 si B/C/D)
1. `studio_prepare(project_dir, intent)` — **Scénarios B/C/D seuls** (`Samples/`, `remix`, `lyrics`). Toujours `project_dir="projects/<nom>"`.
2. `studio_compose(project_dir, strudel_code, palette_id="auto", intent="...", auto_master=true, auto_prepare=true, render_mode="full")` → eval Node → `ScoreIR` → **vrais samples `samples/` en priorité absolue** → boucle ascendante 4 niveaux (0 token) → master + `handoff.txt` (`palette=… samples=N/N`, `BU=PASS(4/4)`). `intent=` (ou `@meta intent="..."`) pilote la palette ; `render_mode="preview"` = validation complète + sonde 12 mesures, sans rendu full.
3. Retouche : `studio_refine(project_dir, semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` ou `strudel_patch={"replace_track":"lead","code":"..."}`.

## Contrat `composition.strudel` & syntaxe (NE PAS copier la mélodie/grille !)
- En-tête 1 ligne : `// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4 [seed=1337] [ts=4/4] [dur=180s] [intent="..."]` ; durée : `bars = round(dur_s * bpm / (60 * B_bar))`, `B_bar = num*4/den` (4/4→4, 3/4→3, 6/8→3).
- **Originalité obligatoire** : varier tonalité, progression, BPM, `kit(...)`, motif Lead. Primitives seules : `grid(...)`, `sectionGrid({...})`, `deg(...)`, `bassline(...)`, `kit(...)`, `transition(...)`, `.in()/.only()/.muteIn()/.lift()`.

```js
// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4
kit("trap_half", {var: 2, hats: "rolls"})
$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7).muteIn("intro,outro")
$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,chorus,outro")
$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.6).in("verse,chorus")
```

## 6 règles absolues
1. `1 piste $ = 1 rôle atomique` ; jamais de notes brutes.
2. Toujours `project_dir="projects/<nom>"` et `@meta` avec `form=` + `.in()`/`.muteIn()`.
3. **100 % vrais samples `samples/`** (`palette_id="auto"` ; `samples=N/N`, `BU=PASS(4/4)` dans `handoff.txt`) ; `clap=OFF(dsp_fallback)` = filtrage sémantique dégradé.
4. Production = MCP `ai-studio-prod` seul (3 outils, < 1 800 tokens) ; jamais les 5 serveurs bas niveau.
5. Pré-flight 0 rendu : `preflight_strudel_gate.py <projet>` (`gf>=0.60`, `sw>=0.65`, `rest 0.20-0.50`) ; lint ≤ 2 lignes `ERR L..:..`.
6. v4.3 : **plus de redémarrage de session après `git pull`** (worker MCP éphémère) ; `RESTART session agent` ne survient plus que si la signature des 3 outils change.
