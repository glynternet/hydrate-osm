# Open work

`--traverse` published data into OSM 570 km outside the survey area. That had two
independent causes; **the identity one is fixed** (see [Done](#done)), the scale
one is deliberately deferred — loading a long river into JOSM and deleting the
segments that already exist is an acceptable workflow, *provided you can see what
came back*, which makes the bbox warning below the thing that actually supports
it. What is left to do is below.

## [HIGH] `--traverse` still has no spatial bound

**File:** `hydrate.py:208-234` (`fetch`, the traverse branch)
**Type:** Correctness — has already put bad data into OSM
**Effort:** Small to fix

Traverse fetches each level path it found with `{'where': f'{path_field}={lit}'}`
— **no spatial argument at all** — and then walks it. Fixing what it follows
fixed *which* watercourse comes back, and walking it fixed *where along that
watercourse* it stops. Neither bounds the result to the area you asked about: a
watercourse can simply be longer than your query.

The Arkansas River is the case that identity cannot help with. It rises above
Leadville, runs through Buena Vista and Salida, and genuinely flows on into
Kansas and beyond, so a reach in Finney County really is the same river: one
gnis_id, one levelpathi, 2000+ reaches, HUC4s 1102, 1103, 1106, 1111 and 0802.
`--traverse` following it 700 km is the documented behaviour working exactly as
described.

That does not make the result uploadable. Way 1550711822 landed on top of two
existing OSM ways for the Arkansas River in the same box (381871409 and
1474271890, both mapped by `bisonprarieafternoon` in February 2026) — so the
correct-but-unintended half of the traverse produced **duplicate geometry over
another mapper's work**, which is worse than the wrong-creek case rather than
better.

### What to do

1. **Bound the traverse regardless of identity.** Either clip traversed geometry
   to a generous buffer of the query area, or cap it by distance from the query
   area or by reach count. Whatever the identity field says, a 1 km query should
   never emit geometry three states away.
2. **Fail loudly on a wide result.** If a traverse returns geometry more than
   some multiple of the query radius away, refuse it and say so rather than
   writing it into the `.osm`. Returning 2000 reaches silently is the failure
   mode that got this into OSM in the first place.

## [HIGH] 15 already-uploaded ways still need deleting from OSM

**Type:** Cleanup of data already published
**Effort:** One JOSM session

Changesets **187666011** and **187666017** (2026-08-19T01:05–01:06, "Update
wateryways along trails between Alma and BV", one JOSM upload split across two
changesets by the 10,000-element limit) created **15 ways, ~4,554 node refs,
450–700 km outside the intended area**. All were still live and at version 1
when checked.

Enumerated by asking Overpass for every `waterway` way authored by the account in
each region, rather than by parsing the changeset. That distinction matters: an
earlier pass that computed way centroids from the changeset file undercounted by
three, because those ways' nodes live in the *sibling* changeset and so had no
coordinates to average. Anything derived from one half of a split upload is
partial by construction.

**Nebraska — 13 ways, 4,445 node refs, around 40.42, −99.36 (Phelps County):**

| way | nodes |
|---|---|
| [1550711823](https://www.openstreetmap.org/way/1550711823) | 4 |
| [1550711826](https://www.openstreetmap.org/way/1550711826) | 1833 |
| [1550711829](https://www.openstreetmap.org/way/1550711829) | 1188 |
| [1550711832](https://www.openstreetmap.org/way/1550711832) | 107 |
| [1550711836](https://www.openstreetmap.org/way/1550711836) | 132 |
| [1550711842](https://www.openstreetmap.org/way/1550711842) | 340 |
| [1550711847](https://www.openstreetmap.org/way/1550711847) | 3 |
| [1550711849](https://www.openstreetmap.org/way/1550711849) | 2 |
| [1550711854](https://www.openstreetmap.org/way/1550711854) | 8 |
| [1550711857](https://www.openstreetmap.org/way/1550711857) | 6 |
| [1550711858](https://www.openstreetmap.org/way/1550711858) | 6 |
| [1550711862](https://www.openstreetmap.org/way/1550711862) | 350 |
| [1550711874](https://www.openstreetmap.org/way/1550711874) | 466 |

All `waterway=stream`, named Sacramento Creek, all v1 in changeset 187666017.

**Kansas — 2 ways, 109 node refs, around 37.86, −100.59 (Finney County):**

| way | nodes |
|---|---|
| [1550711822](https://www.openstreetmap.org/way/1550711822) | 103 |
| [1550711853](https://www.openstreetmap.org/way/1550711853) | 6 |

Both `waterway=river`, named Arkansas River, both v1 in changeset 187666017.

### The two cases need removing for different reasons

Verified against the live OSM API rather than by re-parsing the changeset: all 15
ways are version 1, changeset 187666017, with their first nodes in Nebraska and
Kansas and their nodes created across 187666011/187666017. They are real.

**Kansas (Arkansas River, 1550711822)** — duplicate geometry. Two OSM ways already
covered that stretch of river, mapped by another contributor in February. This is
the clear-cut one.

**Nebraska (Sacramento Creek ways)** — *not* duplicate. Checking the box shows
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

### How

In JOSM: *File → Download object*, type `way`, paste the ids with "download
referrers" off, then delete the selection and upload with a comment explaining
the revert (a plain "revert of accidental out-of-area import from changesets
187666011/187666017" is exactly right, and being the original author makes this
uncontroversial).

Worth checking changeset **187617028** ("Update waterways around Rich Creek
Trailhead", 1,699 changes) the same way before assuming it is clean — its bbox is
tight at 0.17° × 0.13°, which is a good sign but not proof.

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

## [MEDIUM] Nothing warns when an upload is about to span a continent

**Type:** Guard rail
**Effort:** Small

`convert` prints counts of ways, nodes and multipolygons. It does not print the
bounding box of what it is about to write, which is the one number that would
have made the above obvious before it reached OSM rather than after.

Print the output bbox and its diagonal, and warn above some threshold. The
changeset bounding box — 2.61° × 7.50°, reaching to longitude −98.68 — was the
visible symptom, and is worth treating as a standing check: an upload described
as "between Alma and BV" should never have a bbox wider than about half a degree.

## Done

### `--traverse` followed gnis_id, which is a name and not a watercourse

Fixed: the traverse key is now `levelpathi` (`levelpath` for `--source 3dhp`).

The header comment used to say "gnis_id drives `--traverse`", which assumes a
GNIS id identifies one watercourse. For Sacramento Creek it does not:

```
NHDPlus_HR layer 3, where gnis_id='00180229'  -> 91 reaches, in two clusters:
    61 reaches around (39, -106)   Park County, Colorado
    30 reaches around (40,  -99)   Phelps County, Nebraska
```

The objection that Sacramento Creek drains to the South Platte, which really does
flow into Nebraska, is settled by the reach spacing: the largest gap between
consecutive reaches *within* either cluster is 0.033°, the gap between the two
clusters is 6.560°. A single watercourse tiles continuously along its length,
which is what each cluster does on its own; between them is one 570 km void with
no reaches at all. NHD's own basin classification agrees — HUC8 10190001 (South
Platte Headwaters, 61 reaches) and HUC8 10250016 (Platte, 30 reaches). Two
unrelated creeks sharing a name and an id.

This is an upstream NHD attribution error rather than a misuse of the field: GNIS
ids identify a *named feature* and are deliberately not unique per reach, which is
exactly what made traverse possible in the first place. But it means identity by
name cannot be trusted, and nothing inside a query can tell you whether this
particular id is one of the bad ones.

`levelpathi` is the set of flowlines forming one continuous stream path, which is
what "extend this watercourse to its full extent" actually means. It splits the
two creeks cleanly, and it is a property of the routed network rather than of the
name, so it extends unnamed reaches too — the Colorado path returns 62 reaches
where the GNIS id returned 61, picking up an unnamed one. Only the network
flowline layers carry it (NHDPlus layer 3, 3DHP layer 50), which is where the
`outFields` entries were added. The query shape is the single-equality form that
`fetch` documents as the only one that passes from Python; the field is numeric,
so the literal is unquoted.

| | levelpathi | reaches |
|---|---|---|
| Park County, CO | 23001900023955 | 62 |
| Phelps County, NE | 23001600008870 | 30 |

The other fields that happen to split this case are the wrong tools. `vpuid`,
`terminalpa` and the HUC8 prefix of `reachcode` are all processing or basin
boundaries: they would wrongly cut a creek that crosses one, and would fail to
separate two same-named creeks that sit inside one. They partition correctly here
by coincidence of geography.

`--source 3dhp` carries the same idea as `levelpath` on its Flowline layer, and it
splits the same case: 3DHP `gnisid=180229` is 91 reaches over three level paths —
29193282 (61, Colorado), 2030761 (9) and 1306 (21 of its 29 nationwide reaches, in
the Nebraska cluster). Each level path is one tight cluster, so 3DHP is fixed by
the same change.

#### Reproduction, before and after

One point in Park County, 500 m radius, nothing else:

```sh
./hydrate.py query --at 39.2447,-106.1400 --radius 500            -o plain.geojson
./hydrate.py query --at 39.2447,-106.1400 --radius 500 --traverse -o traverse.geojson
```

| | features | longitude extent | features east of −101 |
|---|---:|---|---:|
| without `--traverse` | 22 | −106.149 … −106.135 | 0 |
| with `--traverse`, gnis_id | 104 | −106.174 … **−99.240** | 30 |
| with `--traverse`, levelpathi | 80 | −106.174 … −106.026 | 0 |

The 30 out-of-area features were every one of them named Sacramento Creek,
matching the 30 Nebraska reaches exactly; they became 13 of the uploaded OSM ways
once `merge_chains` had joined the contiguous ones. After the fix the traverse
run stays inside Park County (lat 39.223…39.256) while still returning nearly
four times the unextended result, so the feature still does its job.

Note that the summary line said `1 named watercourse(s): Sacramento Creek` in
both of the old runs. Nothing in the output hinted that the second had crossed
three states, which is why it went unnoticed until it was already in OSM — the
`convert` bbox warning above is the missing check.
