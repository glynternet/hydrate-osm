# Open work

## [HIGH] `--traverse` has no spatial bound, and gnis_id is not unique

**File:** `hydrate.py:190-209` (`fetch`, the traverse branch)
**Type:** Correctness — has already put bad data into OSM
**Effort:** Small to fix, larger to clean up

`--traverse` collects `gnis_id` values from the features found in the query area,
then re-queries each one with `{'where': f"{id_field}='{gid}'"}` — **no spatial
argument at all**. The code comment explains why it is one request per id; what
it does not say is that each of those requests is unbounded.

That unboundedness causes two *different* problems, which are worth keeping
apart because only one of them is a data-identity bug.

### a) gnis_id is not unique per watercourse

The header comment says "gnis_id drives `--traverse`", which assumes a GNIS id
identifies one watercourse. For Sacramento Creek it does not:

```
NHDPlus_HR layer 3, where gnis_id='00180229'  -> 91 reaches, in two clusters:
    61 reaches around (39, -106)   Park County, Colorado
    30 reaches around (40,  -99)   Phelps County, Nebraska
```

The obvious objection is that Sacramento Creek drains to the South Platte, which
really does flow into Nebraska — so perhaps these are one watercourse after all.
The reach spacing settles it:

```
largest gap between consecutive reaches *within* either cluster:   0.033 deg
the gap between the two clusters:                                  6.560 deg
```

A single watercourse tiles continuously along its length, which is exactly what
each cluster does on its own. Between them is one 570 km void with no reaches at
all. NHD's own basin classification agrees:

```
HUC8 10190001  61 reaches   South Platte Headwaters (Park County, CO)
HUC8 10250016  30 reaches   Platte (Phelps County, NE)
```

Two unrelated creeks, in different subbasins, sharing a name and an id — so
traversing the one near Alma drags in the one in Nebraska.

**This is an upstream NHD attribution error, not a misuse of the field.** GNIS ids
identify a *named feature*, and are deliberately not unique per reach — one creek
is many reaches all carrying the same id, which is precisely what makes traverse
possible. Two distinct creeks in two states and two subbasins should carry two
distinct ids; that they do not is a defect in NHD's attribution.

The conclusion that matters for the fix: **the answer is not a better id field.**
Any identity attribute can be wrong upstream, and there is no way to tell from
inside a query whether this particular id is one of the bad ones. The spatial
bound has to stay on regardless of how trustworthy identity looks, because it is
the only constraint that fails safe.

The README's claim that "roughly 30% of reaches carry no GNIS id, so nothing can
extend them" is describing the same field and is correspondingly optimistic: the
70% that *do* carry one can extend to another state.

### b) Traverse working correctly is still not what you want to upload

The Arkansas River case is **not** a collision. It rises above Leadville, runs
through Buena Vista and Salida, and genuinely flows on into Kansas and beyond, so
a reach in Finney County really is the same river. `--traverse` following it 700 km
is the documented behaviour working exactly as described.

That does not make the result uploadable. Way 1550711822 landed on top of two
existing OSM ways for the Arkansas River in the same box (381871409 and
1474271890, both mapped by `bisonprarieafternoon` in February 2026) — so the
correct-but-unintended half of the traverse produced **duplicate geometry over
another mapper's work**, which is worse than the Nebraska case rather than better.

Traverse on a major river needs a distance or reach-count ceiling regardless of
identity being right, and anything it returns outside the survey area needs
checking against existing OSM before upload, not after.

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

**Nothing else in these changesets is wrong.** The trail work in them is sound:
Sheep Creek Trail now runs as a seven-way chain split at three `bridge=yes`
crossings (1550711796 → 1550711797 → 169345446 → 1550711798 → 1550711799 →
1550711800 → 1550711801), each pair sharing an endpoint node, which is exactly how
a way carrying bridges should be split. An earlier pass over this data claimed two
of those ways were duplicates; that was an artefact of the checking script
comparing geometry assembled from only the nodes present in one of the two
changesets, so ways whose surviving fragment was a single shared node came out
looking identical. Partial geometry from missing nodes is its own hazard — the
same failure mode that makes `Coords`-style "skip the nodes we don't have" helpers
quietly dangerous.

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

### The two cases need removing for different reasons

Verified against the live OSM API rather than by re-parsing the changeset: all 12
ways are version 1, changeset 187666017, with their first nodes in Nebraska and
Kansas and their nodes created across 187666011/187666017. They are real.

**Kansas (Arkansas River, 1550711822)** — duplicate geometry. Two OSM ways already
covered that stretch of river, mapped by another contributor in February. This is
the clear-cut one.

**Nebraska (11 Sacramento Creek ways)** — *not* duplicate. Checking the box shows
only two pre-existing waterway ways, neither of them this creek, so the import
added genuinely new and probably accurate USGS geometry. The geometry is not the
problem; the provenance is. It is an unreviewed bulk import into a region nobody
involved has surveyed, made as the side effect of a bug, with no import plan and
no local consultation — which is what OSM's automated-edits norms exist to
prevent, independently of whether the data happens to be good.

Worth noting one of those two pre-existing ways was created by `SomeoneElse_Revert`,
an account used for reverting problem edits. The area has seen this before and is
watched.

So: remove both sets, but do not describe the Nebraska one as fixing bad data. It
is withdrawing data that was never yours to add.

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
