# DOCUMENTATION_IA — tout ce qu'il faut pour produire un morceau (et rien d'autre)

Ce dossier est **la seule lecture nécessaire pour générer un morceau** avec ce framework.
Il remplace la lecture des 8 facettes `docs/` (réservées au développement du projet).
Budget total : ~4 documents compacts, code-first.

## Les 4 documents

| Document | Quand le lire | Contenu |
|---|---|---|
| [01_COMPOSER_AVEC_STRUDEL.md](01_COMPOSER_AVEC_STRUDEL.md) | Avant d'écrire `composition.strudel` | Contrat Strudel v4.1 (`@meta`, prélude), exemple canonique testé, pre-flight du gate, tables degrés/b2-safe/apex/`slow(N)`, recettes anti-échec |
| [02_PRODUIRE_LE_MORCEAU.md](02_PRODUIRE_LE_MORCEAU.md) | Pour appeler les outils MCP | Façade `ai-studio-prod` (exactement 3 outils), workflow 2 tours, scénarios A/B/C/D, retours (`handoff.txt`), qualité du mix (solveur, calibration, corridors, PQ) et des samples (gate v2) |
| [03_SONS_AGRÉABLES.md](03_SONS_AGRÉABLES.md) | Si le rendu sonne désagréable | Diagnostic « sons mal choisis, rythme bon » + gate d'agréabilité (PASS/FIX/SWAP), cohérence de palette, recherche de samples par défaut, consignes |
| `README.md` (ce fichier) | Toujours en premier | Workflow global + 5 règles absolues |

## Workflow global (Scénario A, création originale — le plus courant)

1. `studio_compose(project_dir, strudel_code, auto_prepare=true)` — **1 seul appel** : le code Strudel est évalué headless (Node pur), converti en `ScoreIR` + garde-fous 0 token, blueprint auto-valide, rendu master si Gridline présent.
2. Lire le retour (≤400 cars) + `<projet>/handoff.txt` : `preflight=PASS/FAIL gf=… sw=… rest=…` dit si le MIDI Gate passera.
3. Si `preflight=FAIL` : corriger le motif dans `composition.strudel` (voir [01](01_COMPOSER_AVEC_STRUDEL.md)), pré-vérifier en 0 rendu avec `scripts/preflight_strudel_gate.py <projet>`, puis re-composer.
4. Retouche fine : `studio_refine(project_dir, semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` (~20–40 tokens) ou `strudel_patch={"replace_track": "lead", "code": "..."}`.
5. `studio_prepare` n'est utile que pour les scénarios B (samples verrouillés), C (remix), D (chant) — voir [02](02_PRODUIRE_LE_MORCEAU.md).

## 5 règles absolues

1. `1 piste $ = 1 rôle atomique` (`kit()` émet `$kick/$snare/$hihat/$perc` seul).
2. **Jamais de noms de notes bruts** (`c2`, `f1`…) : uniquement `grid(...)` / `deg(...)` / `bassline(...)`.
3. Toujours un en-tête `// @meta ... form=I4-V8-C8-O4` + présence par section (`.in()`/`.only()`/`.muteIn()`).
4. Production = serveur MCP `ai-studio-prod` **seul** (3 outils) ; jamais les 5 serveurs bas niveau.
5. Erreur Strudel : ≤ 2 lignes `ERR L..:..` du lint → corriger et relancer ; pas de stacktrace.

## Pour aller plus loin (développement uniquement)

`docs/` reste la référence développement : [docs/00_SOMMAIRE.md](../docs/00_SOMMAIRE.md) (index complet), [docs/06_PONT_STRUDEL_HEADLESS.md](../docs/06_PONT_STRUDEL_HEADLESS.md) (pont), [docs/07_GUIDE_CONCEPTION_STRUDEL.md](../docs/07_GUIDE_CONCEPTION_STRUDEL.md) (guide détaillé anti-erreurs), [docs/AGENT_RUNBOOK_V4.md](../docs/AGENT_RUNBOOK_V4.md) (runbook tokenisé ≤750 tokens), [docs/05_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md](../docs/05_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md) (mix), [docs/08_AUDIO_INTELLIGENCE_V2.md](../docs/08_AUDIO_INTELLIGENCE_V2.md) (samples v2).
