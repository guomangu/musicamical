# DOCUMENTATION_IA — Runbook Production v4.2 (2 documents, <= 750 tokens)

Seule lecture nécessaire pour produire un morceau (remplace les 5 docs dev [`docs/`](../docs/00_SOMMAIRE.md)) : **2 documents** — ce fichier (`README.md`, préfixe 100 % statique pour prompt caching) + [01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md) (guide complet : Strudel, façade 3 outils, diagnostic timbres, boucle ascendante, bonnes pratiques).

## Flux (1 appel `auto_prepare=true`, ou 2 si B/C/D)
1. `studio_prepare(project_dir, intent)` — **Scénarios B/C/D seuls** (`Samples/` projet, `remix`, `lyrics`). Toujours `project_dir="projects/<nom>"` (verrouillé automatiquement dans `projects/`).
2. `studio_compose(project_dir, strudel_code, palette_id="auto", auto_master=true, auto_prepare=true)` → eval Node headless → `ScoreIR` → **vrais samples `samples/` en priorité absolue** (dérivés `±12 st` dans `derived_samples/`) → **boucle d'analyse ascendante 4 niveaux** (`bottom_up_analyzer.py` : **Sons → Accords → Pistes → Morceau**, 0 token) → master + `handoff.txt` (`palette=… samples=N/N`, `BU=PASS(4/4)`).
3. Retouche : `studio_refine(project_dir, semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` ou `strudel_patch={"replace_track":"lead","code":"..."}`.

## Contrat `composition.strudel` & exemple canonique (testé CI, 0 fix)
- En-tête 1 ligne : `// @meta 140 C:minor trap_rap V=0.0 form=I4-V8-C8-O4 [seed=1337] [ts=4/4]` ; durée : `bars = round(180 * bpm / 240)`.
- Primitives seules (jamais de notes brutes `c2`, `f1`…) : `grid(...)`, `deg(..., oct)`, `bassline(..., {mode:"root|stepwise", glide:40})`, `kit("trap_half", {var:2, hats:"rolls"})`, `.in()`/`.only()`/`.muteIn()`.

```js
// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4
kit("trap_half", {var: 2, hats: "rolls"})
$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7).muteIn("intro,outro")
$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,chorus,outro")
$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.6).sometimesBy(0.25, x => x.degradeBy(0.5)).in("verse,chorus")
```

## 6 règles absolues
1. `1 piste $ = 1 rôle atomique` (`kit()` émet `$kick/$snare/$hihat/$perc` seuls) ; jamais de notes brutes.
2. Toujours `project_dir="projects/<nom>"` et `@meta` avec `form=` + `.in()`/`.muteIn()`.
3. **100 % vrais samples `samples/`** (`palette_id="auto"` ; vérifier `samples=N/N` et `BU=PASS(4/4)` dans `handoff.txt`).
4. Production = serveur MCP `ai-studio-prod` seul (3 outils, < 1 800 tokens combinés) ; jamais les 5 serveurs bas niveau.
5. Pré-flight 0 rendu : `preflight_strudel_gate.py <projet>` (`gf>=0.60`, `sw>=0.65`, `rest 0.20-0.50`) ; lint ≤ 2 lignes `ERR L..:..`.
6. Après chaque `git pull` / `install.sh` : **redémarrer la session agent** (sinon alerte `RESTART session agent`).
