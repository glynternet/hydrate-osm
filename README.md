# hydrate-osm

Pull USGS hydrography into OpenStreetMap-ready geometry, so that under-mapped
areas stop hiding their water.

## Why

Standing at a trailhead with a creek running 50 m away, and your routing tools
show nothing — because OSM has nothing. The water is real; the map is empty.

That is common across large parts of the rural US. Most American streams in OSM
arrived via a bulk NHD import around 2007–09, and wherever that import didn't
reach, the hydrography was simply never drawn. Trails get mapped because people
walk them. Creeks don't, because nobody thinks to.

USGS has the data, in the public domain, at 1:24,000. `hydrate-osm` fetches it,
converts it to correctly-tagged OSM geometry, and hands you a file to review in
JOSM before any of it touches the map.

A worked example: at Rich Creek Trailhead in Park County, Colorado, OSM has no
waterway within 1.5 km. USGS has 23 reaches within 1 km, the nearest 2 m from
the guidepost.

## Install

Nothing to install. One file, Python 3.8+, standard library only.

```sh
curl -O https://raw.githubusercontent.com/glynternet/hydrate-osm/main/hydrate.py
chmod +x hydrate.py
```

## Use

Two steps, deliberately separate so you can look at what came back before
converting it.

```sh
./hydrate.py query --at 39.0681575,-106.1164971 --radius 1000 -o water.geojson
./hydrate.py convert water.geojson water.osm
```

Then open `water.osm` in JOSM, review it against aerial imagery, and copy what
you've checked into a downloaded OSM layer.

### Three workflows

**A place you care about** — a trailhead, a camp, a water stop.

```sh
./hydrate.py query --at LAT,LON --radius 1000 --traverse -o here.geojson
```

`--traverse` extends each watercourse found to its whole length instead of
slicing it at the search radius, so ways end at confluences rather than at an
arbitrary circle edge. Unnamed reaches extend too, which is worth having: about
30% of reaches carry no name, and they are mostly the headwaters.

It works on the routed network rather than on names. Each reach found is fetched
along with its whole *stream path* — the chain of flowlines NHD groups under one
`levelpathi`, or `levelpath` in 3DHP — and the path is then walked outwards from
the reaches actually found, stopping where the name changes.

Both halves are load-bearing, and each fixes a way the other overshoots:

- Names alone are ambiguous. A GNIS id names a feature rather than identifying
  one, and NHD gives the same id to distinct watercourses that share a name:
  `gnis_id='00180229'` is two unrelated Sacramento Creeks, one in Park County CO
  and one 570 km away in Phelps County NE. Traversing by name put Nebraska
  geometry into a 500 m Colorado query, and then into OSM.
- Paths alone are too long. A level path runs from a headwater to the outlet of
  its basin, through every name change on the way, so a small tributary drags in
  the mainstem it drains into. The 1 km Rich Creek query below touches the South
  Fork South Platte, whose path continues as the South Platte River: 2,149
  reaches ending in central Nebraska.

Walking the network gets both. It also handles unnamed reaches without a special
case, which is worth being explicit about because it looks like it should need
one: an unnamed reach is treated as having the name "no name", so the single rule
— *keep going while the name is unchanged* — extends it to the ends of its
unnamed run and stops at the first named reach.

That is something a filter could not have done. The unnamed reaches on a path are
not one stretch; asking the service for all of them returns fragments scattered
along its whole length, 570 km of it in the Rich Creek case. Only a walk knows
which unnamed reaches are the ones you are standing next to.

Expect traverse to multiply the result: the 1 km query near Rich Creek goes from
33 features to 246, because the South Fork South Platte alone is 184 reaches over
72 km. Check the extent of what comes back before converting it. A long river is
still a long river — traverse stops where a watercourse ends, not where your
query area does, and nothing yet clips it back.

**A route** — everything within reach of a GPX track.

```sh
./hydrate.py query --track ride.gpx --radius 500 -o route.geojson
```

The whole track becomes one buffered query per layer rather than dozens of
overlapping circles. Track points are thinned to half the radius first, since
vertices closer together than that add nothing to a buffered query and plenty
to the request body.

**A watershed** — systematically filling in an area.

```sh
./hydrate.py huc-at 39.0681575,-106.1164971    # -> 101900010202
./hydrate.py query --huc 101900010202 -o huc.geojson
```

HUC12 is the natural unit: it is how NHD is organised, and its boundary is a
real watershed divide rather than an arbitrary box. Queried against the true
polygon, not its bounding box — for the Rich Creek subwatershed that is the
difference between 680 reaches and 1,157.

Ephemeral reaches are dropped by default in this mode (`--ephemeral keep` to
override). They only run after rain, so they are useless for finding water, and
mapping every ephemeral wash is the kind of clutter that draws objections.

## What it produces

| source | OSM tags |
|---|---|
| Stream/River, perennial | `waterway=stream` or `river` |
| Stream/River, intermittent or ephemeral | + `intermittent=yes` |
| Canal/Ditch | `waterway=ditch` |
| Lake/Pond | `natural=water` + `water=pond` |
| Reservoir | `natural=water` + `water=reservoir` |
| Swamp/Marsh | `natural=wetland` + `wetland=marsh` |
| Spring/Seep | `natural=spring` |
| Pipeline, underground conduit | dropped |

Beyond the tag mapping, `convert` does four things that matter:

**Deduplicates nodes**, so reaches meeting at a junction share a node instead of
stacking two on top of each other. Without this you upload a network that looks
right and is topologically disconnected.

**Merges contiguous reaches** carrying identical tags. NHD splits watercourses
into short reaches for its flow model; OSM doesn't want that. Merging happens
only where it is unambiguous — among ways sharing a tag set, exactly one
arriving at a node and exactly one leaving. That keeps confluences intact (a
tributary ending on an intermediate node is fine and normal in OSM) while
refusing the genuinely undecidable cases: a divergence, or two same-named
channels flowing into each other. Ways are never reversed, because the source
digitises in flow direction and OSM wants waterways drawn downstream.

**Builds multipolygons** for waterbodies with islands. One marsh near Rich Creek
has 23 of them; a bare outer ring would claim all 23 as water.

**Keeps waterbody connectors.** Where a stream threads through a pond, these
carry the flow line across it. Dropping them as "artificial" fractures the
watercourse into disconnected pieces.

## Stream or river?

Neither USGS product says. NHDPlus lumps everything under FType 460
"StreamRiver"; 3DHP has a `River` type but applies it to ~35k features
nationally out of 22M, so it means "major river", not OSM's threshold.

OSM's rule is that *a stream can be jumped across by an active, able-bodied
person*, suggested as under 3 m — and for varying watercourses, judged at **high
water**. Two signals are available:

- **the name**, which describes the whole named feature and is stable. US
  toponymy is consistent enough to use: Creek, Brook, Run, Branch and Gulch are
  small; River is not.
- **`qama`**, modelled mean annual flow in cfs, which describes *this reach* and
  varies enormously — the South Fork South Platte runs 0.18 to 124 cfs over its
  length.

The name wins where both are available, since it matches how surrounding
features are already tagged. `qama` is used to catch disagreements: where the
name says one thing and the flow says the other, the feature gets a
`fixme` tag rather than a silent guess. Search `fixme` in JOSM to review them.

The middle band (10–30 cfs) is treated as inconclusive on purpose.
Flow-to-width conversion is regional and noisy, and a hard single threshold
would split rivers arbitrarily wherever `qama` happened to cross it. Note also
that `qama` is a *mean annual* figure, so in a snowmelt catchment it
understates the high-water width OSM asks you to judge by.

## Reviewing and uploading in JOSM

### Set up two layers

1. Open the `.osm` — it loads as an editable layer, all objects new (negative ids)
2. **File → Download from OSM** for the same area, and use **"Download as new
   layer"**. Without that, real OSM data merges *into* your import layer, and
   once positive and negative ids are intermingled, deleting a leftover from the
   import can quietly delete a real OSM object instead.
3. **Imagery → Bing** or **Esri World Imagery**. Check alignment before you
   trust any geometry — see the datum note under Gotchas.

You now have a scratch layer (the import) and an upload layer (real OSM). Only
what you deliberately copy across ever reaches the upload.

**Download the upload layer from the live API, not from an extract.** BBBike and
Geofabrik extracts are cut from planet dumps on a schedule, so the file is hours
to days behind live OSM however recently you fetched it — you will collide with
whoever edited in between. Two further problems make this a firm rule rather
than a preference:

- **Clipped ways.** A way running past the extract boundary may be present only
  as the portion inside it, and a plain `.osm` file cannot say "this is
  truncated". Modify it, upload, and you delete every node outside the extract.
  Version checking will not catch this: if nobody else touched that way its
  version still matches, so the damage uploads cleanly under your name.
- **Update data does not work on them.** It re-downloads the area the layer
  covers, and an extract covers a whole region. The API caps a download at
  0.25 square degrees, so it just refuses.

Extracts are good for reference and analysis. For anything you intend to upload,
use **File → Download from OSM** (`Ctrl+Shift+D`) over a small bbox.

Uploading never reverts OSM wholesale — only objects you created, modified or
deleted are sent, and the API rejects any whose version has moved on, which is
what raises the conflict dialog. But it will not save you from resolving a
conflict by taking your version and discarding someone else's work. Keep the
window small: download immediately before editing, upload promptly after, and
split large jobs into per-watercourse changesets so there is less time and less
surface for anyone else to have touched the same objects.

### When the download area is too large

JOSM refusing a download is what sends most people to an extract, so deal with
it directly instead.

**First, check whether it really is too large.** The API caps a single download
at **0.25 square degrees** and around **50,000 nodes**. Under-mapped rural
country is node-sparse, so an area that feels big often is not: the Rich Creek
HUC12 is 132 km2, which works out at 0.026 square degrees — a tenth of the
limit — holding under 17,000 nodes. It downloads in one request. If JOSM
complained, check what you actually selected; refreshing a layer that came from
a region-sized file will fail even though the area you care about would not.

When it genuinely is too large, in order of preference:

**Download along a track.** With a GPX loaded, JOSM's *Download along* action
splits the corridor into API-sized chunks and fetches them all. This pairs
directly with `query --track`: same GPX, same corridor, and you pull data only
where you will actually edit.

**Several adjacent bboxes into one layer.** Download, pan, download again into
the *same* layer — JOSM merges them. Unglamorous, completely current, and it
keeps you honest about how much you are taking on.

**Overpass, via the download dialog.** JOSM's download window has an Overpass
tab beside the OSM one. Overpass runs about a minute behind live rather than
days, and lets you filter, so you can pull just waterways and highways across a
wide area instead of everything.

Treat filtered Overpass results with the same caution as an extract, though:
asking for waterways only means the roads they cross are absent, and objects
arrive without their parent relations. That is fine for reference and riskier
for editing, because JOSM cannot always tell what it was not given. Edit only
objects you know you downloaded whole.

Needing a large download is usually a sign the changeset is heading for too big
anyway. Per-watercourse changesets keep the download small, the review
tractable, and the conflict window short, all at once.

### Copy across what you have reviewed

Select reviewed features in the import layer, `Ctrl+C`, switch to the OSM layer,
then **Edit → Paste at source position** (`Ctrl+Alt+V`, expert mode only — tick
"Expert mode" in Preferences or the action is unavailable).

Three paste actions exist and only one is right here:

| action | effect |
|---|---|
| Paste (`Ctrl+V`) | pastes at the **mouse cursor** — displaces everything |
| Paste Tags (`Ctrl+Shift+V`) | applies the copied objects' **tags to your current selection** |
| Paste at source position (`Ctrl+Alt+V`) | what you want — keeps real coordinates |

Paste Tags is the dangerous one: with a road selected it will happily tag that
road `waterway` + `natural=water`. If a "Conflicts in pasted tags" dialog
appears, you have hit the wrong one — cancel it, press `Esc` to clear the
selection, and use Paste at source position instead.

### Work through it

For anything beyond a handful of features, install the **todo** plugin
(Edit → Preferences → Plugins → search `todo`). Select a slice, add it to the
list, and work through one feature at a time — 489 ways is several sittings,
not one.

Useful searches (`Ctrl+F`):

```
fixme                    the classifier's own uncertain calls
waterway=stream -name=*  unnamed streams
intermittent=yes         seasonal channels
water=pond               waterbodies
```

### Fix the crossings

Validation runs independently of upload, as often as you like. `Alt+Shift+V`
opens the Validation panel; `Shift+V` (or its Validation button) runs the check.

Scope follows the selection, which is what makes it usable on a large batch:
with nothing selected it checks every object in the layer, and with objects
selected it checks only those. Validate the slice you just reviewed rather than
all 489 ways at once — same principle as working through a todo list.

Use the **Fix** button sparingly here. It handles mechanical problems
(duplicate nodes, unclosed ways) well, but the warnings you will see most are
waterways crossing roads, and those have no correct automatic answer.

That crossing warning is the most common thing NHD-derived geometry produces,
because NHD models hydrology and has no opinion about roads. The right fix
depends on what is physically there, so check imagery — do not guess:

| reality | tagging | share a node? |
|---|---|---|
| water runs through a pipe under the road | split the waterway either side of the road; tag the middle section `tunnel=culvert` + `layer=-1` | **no** — they cross at different levels |
| the road spans the water | split the road either side; tag the middle section `bridge=yes` + `layer=1` | **no** |
| vehicles drive through the water | `ford=yes` on the node shared by both ways | **yes** — the node must belong to both |

For an irrigation ditch crossed by a road, a culvert is overwhelmingly the most
likely; ditches are nearly always piped under roads. Reserve `ford=yes` for
crossings you know are fords.

Only a ford needs a shared node, and where two ways cross without one the
easiest fix is the **utilsplugin2** plugin: select both ways, then
**More tools → Add nodes at intersections** (`Shift+I`). It adds a node at every
intersection point and does not split the ways.

Without the plugin: draw a node on one way at the crossing so it becomes part of
that way, select it, and use **Tools → Join Node to Way** (`J`) to pull the other
way through it. `M` (Merge Nodes) fuses two nodes that are already nearly
coincident.

Then tag the shared node `ford=yes` — and check it really is shared before
moving on. With the node selected, its parent-ways list should name *both* the
path and the watercourse. A node belonging to only one of them looks identical
on screen, and that is the whole failure mode.

### Adding a bridge

**Check whether it is already mapped first.** The roads in your area already
exist in OSM; the waterway is what you are adding. If the road already carries
`bridge=yes` over that spot, there is nothing to add — just make sure your new
waterway does not share a node with it. Splitting and retagging an existing road
is a heavier edit than adding new geometry, and it shows up in your changeset as
a modification to someone else's work, so only do it when the bridge is genuinely
missing.

When it is missing, the road is what gets split — never the waterway:

1. Put a node on the road at each end of the bridge deck, where it meets solid
   ground. Snap them to the existing way rather than drawing new geometry.
2. Select both nodes and **Tools → Split Way**. The road becomes three sections,
   all inheriting its existing tags (`highway`, `surface`, `name`, `ref` ...).
3. Select the middle section and add:

```
bridge = yes
layer  = 1
```

`layer` is not optional in practice. It is how renderers and routers know what
passes over what, and for a simple crossing it is almost always `1`. Without it
you have asserted a bridge that is on the same level as the thing it crosses.

**Do not let the bridge share a node with the waterway.** That is the difference
between a bridge and a ford, and it is the single most common way to get this
wrong.

**Do not end a bridge at a junction.** If a side road joins right at the
abutment, put the split slightly clear of it.

More specific values exist where you know the structure: `bridge=viaduct`,
`boardwalk`, `covered`, `trestle`, `movable`, `aqueduct`, `low_water_crossing`.
Prefer `bridge=yes` plus `bridge:structure=*` for engineering types —
`bridge=cable-stayed` is discouraged in favour of
`bridge=yes` + `bridge:structure=cable-stayed`. If you cannot tell from imagery,
`bridge=yes` is the honest answer.

Two things that are *not* bridges: a pipe carrying water under a road is a
culvert on the waterway (`tunnel=culvert`), and `bridge=culvert` should not be
used. Where the lower feature is surrounded by earth, it wants `tunnel=*`, not a
bridge on the upper way.

### Upload

Validate again (`Shift+V`), then **File → Upload data** (`Ctrl+Shift+↑`), with
the **OSM layer active** — JOSM uploads the active layer, so uploading from the
scratch layer sends unreviewed data.

The dialog wants three things:

- **Changeset comment** — what and where, specifically:
  `Add hydrography (streams, ponds) around Rich Creek Trailhead, Park County CO, from USGS NHDPlus HR`
- **Source** — `USGS NHDPlus HR`, on the changeset. Not on each feature: the
  per-feature `NHD:*` tags left by the old import wave are now considered
  clutter.
- **Upload strategy** — a single request under ~1,000 objects, chunks of 1,000
  above. A HUC12 at 489 ways technically fits in one, but split it by
  watercourse anyway so each changeset stays reviewable and revertable.

If it is an import, add `import=yes` and a link to your import wiki page, and
upload from the dedicated import account.

Conflicts (someone edited the same object since you downloaded) open the
conflict resolution dialog rather than failing the upload.

### Afterwards

Overpass sees the data within about a minute; the standard tile layer takes
minutes to an hour. If something went wrong, the **reverter** plugin undoes a
changeset cleanly — better to revert your own mistake than have someone else do
it for you.

## Mapping or importing?

This matters. Reviewing each feature against imagery and drawing what you've
checked is **mapping**, and needs nobody's permission. Bulk-converting a
watershed and uploading it is an **import**, and needs the
[Import Guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines): a
wiki page, a post to `imports@` and to your local community forum, a dedicated
account, and changeset tags pointing at the wiki page.

The line is roughly "could you honestly say you looked at each of these?". A
HUC12 holds several hundred reaches, so the answer there is no unless you have
genuinely spent the evenings.

NHD-derived imports get scrutiny, because the 2007–09 wave left known problems:
geometry not matching imagery, excessive vertex density, artificial paths
confusing later mappers, duplication over hand-mapped data. Expect to be asked
how you conflated and how you checked.

## Gotchas

**Datum.** The two USGS services disagree by a constant ~1.1 m on identical
geometry — NHD is natively NAD83, OSM is WGS84, and they differ in whether they
transform on `outSR=4326`. Well inside the error of a 1:24k source either way,
but align to imagery before uploading rather than trusting either absolute
position.

**Names.** NHDPlus layer 4 (NonNetworkNHDFlowline) often has `gnis_name` null
where 3DHP resolves a name — "Platte Station Ditch" is named in 3DHP and unnamed
in NHDPlus. Only network flowlines reliably carry GNIS ids.

**Some `where` clauses get 403'd from some clients.** Rejected from Python while
succeeding from curl, with a byte-identical request body and across every
header, HTTP version and ALPN combination — so the trigger appears to be an
injection heuristic weighted by TLS fingerprint:

| | |
|---|---|
| `levelpathi=23001900023955` | works |
| `levelpathi=23001900023955 AND streamorde<=4` | works |
| anything naming `gnis_id`, quoted or not, including `IS NULL` | **403** |
| `IN (...)` | **403** |
| `OR` | **403** |

It tracks the field token rather than the clause shape, so rewriting the
condition does not help. This is why `--traverse` issues one query per level
path rather than a single `IN`, and why it filters on the name in Python rather
than in the query.

Note this is *not* the same as the intermittent 403s these services return under
load, which retrying does fix. A request that retries for two minutes and then
fails has hit the permanent kind.

**Paging.** `maxRecordCount` is 2000. Watch `exceededTransferLimit`; this tool
pages automatically, but anything you write by hand against the API should too.

## Data sources

Defaults to **NHDPlus HR**, with `--source 3dhp` available.

Three generations of the same data. **NHD** (late 1990s) digitised the blue
lines off 1:24,000 paper topo maps — the origin of nearly every stream in both
services, retired 2023. **NHDPlus HR** is that same linework with a joint
USGS/EPA modelling layer added: routed network, plus flow, drainage area and
slope per reach. Production halted in 2024; frozen, still served, designated a
"bridge dataset" for the next several years. **3DHP** (2024→) starts over,
deriving channels from lidar-scanned terrain rather than tracing old maps.

Both are live because 3DHP is a *rolling* replacement, region by region. Until
an area is reprocessed, 3DHP re-serves the old NHD linework through a newer
schema that carries fewer attributes. Check `workunitid` on a 3DHP feature: a
value of `NHD` means that area is not lidar-derived yet.

| attribute | NHDPlus HR | 3DHP |
|---|---|---|
| perennial / intermittent | `fcode` | **absent** |
| mean annual flow | `qama` | absent |
| drainage area | `totdasqkm` | absent (announced as future) |
| slope | `slope` | absent |
| Strahler order | `streamorde` | `streamorder` |
| stream path (what `--traverse` fetches by) | `levelpathi` | `levelpath` |
| name (what `--traverse` stops at) | `gnis_id` | `gnisid` |
| up/downstream links (what `--traverse` walks) | `hydroseq`, `up`/`dnhydroseq` | `hydrosequence`, `up`/`dnhydrosequence` |
| network navigation | VAAs | Flow Network Derivatives |

The perennial/intermittent split is why NHDPlus is the default. 3DHP has no
field carrying it and none is announced, so everything from that source arrives
looking perennial — do not use it to judge whether water will be there in
August.

## Limitations

**United States only.** These are USGS services. For the UK the equivalent
open data is OS Open Rivers and Environment Agency LIDAR, both OGL and
OSM-compatible, but this tool does not speak to them.

**The data is 1990s cartography** in most places, not fresh survey. It is a good
guide and a poor authority. Check it against imagery.

**`--traverse` is not bounded by your query area.** It stops where a watercourse
ends, which for a large river is a long way from where you asked. Nothing yet
clips the result back, so check the extent of the GeoJSON before converting it —
this has already put geometry into OSM 700 km from the area being surveyed, and
the changeset bounding box was the only visible sign.

**Nothing here uploads to OSM.** Deliberately. The output is a file for you to
review in an editor, and that review is the whole point.

## Licence

MIT. USGS data is a work of the US federal government and in the public domain;
it is compatible with OSM's ODbL, which is why this approach is viable at all.
