# Open work

## [HIGH] `--traverse` has no spatial bound, and gnis_id is not unique

**File:** `hydrate.py:190-209` (`fetch`, the traverse branch)
**Type:** Correctness — has already put bad data into OSM
**Effort:** Small to fix, larger to clean up

`--traverse` collects `gnis_id` values from the features found in the query area,
then re-queries each one with `{'where': f"{id_field}='{gid}'"}` — **no spatial
argument at all**. The code comment explains why it is one request per id; what
it does not say is that each of those requests is unbounded.

The header comment says "gnis_id drives `--traverse`", which assumes a GNIS id
identifies one watercourse. It does not. It identifies a *name*:

```
NHDPlus_HR layer 3, where gnis_name='Sacramento Creek'
  -> 91 reaches, 1 distinct gnis_id (00180229)
```

Those 91 reaches are not one creek. They are every watercourse in the United
States called Sacramento Creek. So traversing the Sacramento Creek near Alma
pulls in the Sacramento Creek in Nebraska too, and traversing the Arkansas River
at Buena Vista pulls in its reaches in south-western Kansas.

The README's claim that "roughly 30% of reaches carry no GNIS id, so nothing can
extend them" is describing the same field and is correspondingly optimistic: the
70% that *do* carry one can extend nationwide.

### It has already happened

Changesets **187666011** and **187666017** (2026-08-19T01:05–01:06, "Update
wateryways along trails between Alma and BV", one JOSM upload split across two
changesets by the 10,000-element limit) created **12 ways, ~4,543 nodes, 450–700 km
outside the intended area**. All were still live and at version 1 when checked:

| way | name | nodes | location |
|---|---|---|---|
| 1550711822 | Arkansas River | 103 | 37.857, -100.577 — Finney County, KS |
| 1550711823 | Sacramento Creek | 4 | 40.392, -99.260 — Nebraska |
| 1550711826 | Sacramento Creek | 1833 | 40.448, -99.364 |
| 1550711829 | Sacramento Creek | 1188 | 40.409, -99.271 |
| 1550711832 | Sacramento Creek | 107 | 40.445, -99.459 |
| 1550711836 | Sacramento Creek | 132 | 40.438, -99.467 |
| 1550711842 | Sacramento Creek | 340 | 40.426, -99.299 |
| 1550711854 | Sacramento Creek | 8 | 40.429, -99.288 |
| 1550711857 | Sacramento Creek | 6 | 40.445, -99.461 |
| 1550711858 | Sacramento Creek | 6 | 40.457, -99.436 |
| 1550711862 | Sacramento Creek | 350 | 40.452, -99.447 |
| 1550711874 | Sacramento Creek | 466 | 40.383, -99.255 |

The changeset bounding box — 2.61° × 7.50°, reaching to longitude −98.68 — was
the visible symptom, and is worth treating as a standing check: an upload
described as "between Alma and BV" should never have a bbox wider than about
half a degree.

### Fix

1. **Keep the spatial filter on traverse queries.** Pass the original `spatial`
   alongside the where clause, or intersect the traversed result against a
   generous buffer of the query area before returning it. Traverse is meant to
   follow a watercourse past the edge of a circle, not across the country.
2. **Do not rely on gnis_id for identity.** It is a name key. Where a true
   per-feature identity is needed, `nhdplusid` (already used as the dedup key) is
   the one that is unique.
3. **Fail loudly on a wide result.** If a traverse returns geometry more than
   some multiple of the query radius away, that is a bug, not a long river —
   refuse it and say so rather than writing it into the `.osm`.
4. **Correct the README.** The `--traverse` section describes the GNIS id as
   identifying a named watercourse; it identifies a name, nationwide.

### Cleanup

The 12 ways above and their nodes want deleting. In JOSM: *File → Download object*,
type `way`, paste the ids with "download referrers" off, then delete the selection
and upload with a comment explaining the revert (a plain "revert of accidental
out-of-area import from changesets 187666011/187666017" is exactly right, and
being the original author makes this uncontroversial).

Worth checking changeset **187617028** ("Update waterways around Rich Creek
Trailhead", 1,699 changes) the same way before assuming it is clean — its bbox is
tight at 0.17° × 0.13°, which is a good sign but not proof.

## [MEDIUM] Nothing warns when an upload is about to span a continent

**Type:** Guard rail
**Effort:** Small

`convert` prints counts of ways, nodes and multipolygons. It does not print the
bounding box of what it is about to write, which is the one number that would
have made the above obvious before it reached OSM rather than after.

Print the output bbox and its diagonal, and warn above some threshold.
