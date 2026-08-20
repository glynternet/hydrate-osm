#!/usr/bin/env python3
"""hydrate-osm - pull USGS hydrography into OSM-ready geometry.

Two steps, deliberately separate so you can inspect what came back before
converting it:

    hydrate.py query   ...  -o water.geojson
    hydrate.py convert water.geojson water.osm

Then open the .osm in JOSM, review every feature against aerial imagery, and
copy what you have checked into a downloaded OSM layer.

No third-party dependencies: stdlib only, so it runs anywhere Python 3.8+ does.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict, namedtuple

# --- services ----------------------------------------------------------------

NHDPLUS = 'https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer'
DHP = 'https://hydro.nationalmap.gov/arcgis/rest/services/3DHP_all/MapServer'
WBD = f'{NHDPLUS}/12'          # watershed boundaries (HUC12)

# Per source: the service, layer -> fields to request, and the field names
# --traverse needs, since the two schemas spell them differently.
#
#   path_field  the level path, which traverse fetches by
#   name_field  the GNIS id, which traverse stops at
#   seq_field   this reach's position in the routed network
#   up_field    the reach immediately upstream
#   dn_field    the reach immediately downstream
#
# `traverse_path` explains why it uses these and not something simpler.
# nhdplusid/id3dhp is the dedup key.
Source = namedtuple('Source', 'base layers flow_layer path_field name_field seq_field up_field dn_field')

SOURCES = {
    'nhdplus': Source(NHDPLUS, {
        2: 'nhdplusid,gnis_id,gnis_name,ftype,fcode',
        3: 'nhdplusid,gnis_id,gnis_name,ftype,fcode,qama,totdasqkm,streamorde,slope,lengthkm,'
           'levelpathi,hydroseq,uphydroseq,dnhydroseq',
        4: 'nhdplusid,gnis_id,gnis_name,ftype,fcode,lengthkm',
        9: 'nhdplusid,gnis_id,gnis_name,ftype,fcode,areasqkm',
    }, 3, 'levelpathi', 'gnis_id', 'hydroseq', 'uphydroseq', 'dnhydroseq'),
    '3dhp': Source(DHP, {
        20: 'id3dhp,gnisid,gnisidlabel,featuretypelabel',
        50: 'id3dhp,gnisid,gnisidlabel,featuretypelabel,levelpath,streamorder,'
            'hydrosequence,uphydrosequence,dnhydrosequence',
        60: 'id3dhp,gnisid,gnisidlabel,featuretypelabel',
    }, 50, 'levelpath', 'gnisid', 'hydrosequence', 'uphydrosequence', 'dnhydrosequence'),
}

PAGE = 2000                    # maxRecordCount on these services


UA = 'hydrate-osm (+https://github.com/glynternet/hydrate-osm)'

# These services reject intermittently under load - a 403 that has nothing to do
# with the request, since the identical call succeeds moments later. Observed
# rejecting steadily for ~15s at a stretch, so back off well past that rather
# than losing a long --huc sweep to a blip.
#
# There is a second, permanent kind of 403 that retrying cannot help with. These
# services also refuse some where clauses outright when the request comes from
# Python, and no amount of waiting changes that:
#
#   works:    levelpathi=23001900023955
#             levelpathi=23001900023955 AND streamorde<=4
#   403s:     anything naming gnis_id, quoted or not, including IS NULL
#             IN (...)
#             OR
#
# Established by testing, because it looks like throttling and is not. The body
# curl sends is byte-identical to urlencode's, and the rejection survives every
# header combination, both HTTP versions, and ALPN on or off; curl gets away
# with all of these. So the trigger appears to be a SQL-injection heuristic that
# weights the client's TLS fingerprint, and it tracks the field token rather
# than the clause shape - which is why no rewriting of the condition gets around
# it, and why `traverse_path` filters on the name in Python rather than asking
# the service to.
#
# A run that stalls for two minutes and then dies with a 403 has hit this rather
# than load.
RETRY_STATUS = {403, 429, 500, 502, 503, 504}


def post(url, params, attempts=7):
    data = urllib.parse.urlencode(params).encode()
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            print(f'  {e.code} from service, retrying in {wait}s...', file=sys.stderr)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            print(f'  connection problem, retrying in {wait}s...', file=sys.stderr)
        time.sleep(wait)


def query_layer(base, layer, fields, spatial, fmt='geojson'):
    """Query one layer, paging until the server stops truncating."""
    out, offset = [], 0
    while True:
        params = {
            'inSR': 4326, 'outSR': 4326, 'spatialRel': 'esriSpatialRelIntersects',
            'outFields': fields, 'returnGeometry': 'true', 'f': fmt,
            'resultOffset': offset, 'resultRecordCount': PAGE, **spatial,
        }
        d = post(f'{base}/{layer}/query', params)
        if 'error' in d:
            raise SystemExit(f'service error on layer {layer}: '
                             f'{d["error"].get("message")}')
        feats = d.get('features') or []
        out.extend(feats)
        if not d.get('exceededTransferLimit') or not feats:
            return out
        offset += len(feats)


# --- spatial filters ---------------------------------------------------------

def around_point(lat, lon, radius):
    return {'geometry': f'{lon},{lat}', 'geometryType': 'esriGeometryPoint',
            'distance': radius, 'units': 'esriSRUnit_Meter'}


def along_track(points, radius):
    geom = json.dumps({'paths': [[[p[1], p[0]] for p in points]],
                       'spatialReference': {'wkid': 4326}})
    return {'geometry': geom, 'geometryType': 'esriGeometryPolyline',
            'distance': radius, 'units': 'esriSRUnit_Meter'}


def within_huc(huc12):
    d = post(f'{WBD}/query', {'where': f"huc12='{huc12}'", 'outSR': 4326,
                              'returnGeometry': 'true', 'f': 'json'})
    feats = d.get('features') or []
    if not feats:
        raise SystemExit(f'no such HUC12: {huc12}')
    geom = json.dumps({'rings': feats[0]['geometry']['rings'],
                       'spatialReference': {'wkid': 4326}})
    return {'geometry': geom, 'geometryType': 'esriGeometryPolygon'}


def huc_at(lat, lon):
    # Plain point-in-polygon: no distance buffer, which the service rejects at 0.
    d = post(f'{WBD}/query', {'geometry': f'{lon},{lat}',
                              'geometryType': 'esriGeometryPoint',
                              'inSR': 4326, 'spatialRel': 'esriSpatialRelIntersects',
                              'outFields': 'huc12,name,areasqkm',
                              'returnGeometry': 'false', 'f': 'json'})
    return [f['attributes'] for f in d.get('features') or []]


# --- gpx ---------------------------------------------------------------------

def read_gpx(path, min_spacing):
    """Track points from a GPX file, thinned to min_spacing metres.

    Thinning matters: a recorded track can carry tens of thousands of points,
    and vertices closer together than the search radius add nothing to a
    buffered query but plenty to the request body.
    """
    ns = {'g': 'http://www.topografix.com/GPX/1/1',
          'g0': 'http://www.topografix.com/GPX/1/0'}
    root = ET.parse(path).getroot()
    pts = []
    for tag in ('g:trk/g:trkseg/g:trkpt', 'g0:trk/g0:trkseg/g0:trkpt',
                'g:rte/g:rtept', 'g0:rte/g0:rtept'):
        pts = root.findall('.//' + tag, ns)
        if pts:
            break
    if not pts:
        raise SystemExit(f'no track or route points found in {path}')

    kept = []
    for p in pts:
        ll = (float(p.get('lat')), float(p.get('lon')))
        if not kept or haversine(kept[-1], ll) >= min_spacing:
            kept.append(ll)
    if len(kept) < 2:
        kept = [(float(p.get('lat')), float(p.get('lon'))) for p in pts[:2]]
    return kept, len(pts)


def haversine(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((b[1] - a[1]) * 111320 * math.cos(lat),
                      (b[0] - a[0]) * 111320)


# --- fetch -------------------------------------------------------------------

EPHEMERAL_FCODES = {46007}


def feature_key(f):
    p = f['properties']
    for k in ('nhdplusid', 'id3dhp', 'permanent_identifier'):
        if p.get(k):
            return (k, p[k])
    return ('geom', json.dumps(f['geometry']['coordinates'])[:200])


def path_literal(value):
    """Render a level path id for a where clause.

    ArcGIS types levelpathi as a double and hands it back as a JSON number, so
    it goes in unquoted and without the exponent or trailing `.0` that str()
    would give a float. Level path ids are the only values this tool puts into a
    where clause, and they are always numeric, so there is no quoting branch and
    no string ever reaches the query.
    """
    return format(int(value), 'd')


def watercourse_of(props, src):
    """The identity traverse walks on: the GNIS id, with unnamed as `None`.

    Normalising '' to None here is what lets named and unnamed reaches share one
    rule instead of needing a branch - see `traverse_path`.
    """
    return props.get(src.name_field) or None


def traverse_path(reaches, seeds, src):
    """Walk out from the seed reaches, keeping the run that shares their name.

    Why a walk, rather than a filter on some field:

    A level path - the chain of flowlines NHD groups under one `levelpathi` - is
    the smallest unit these services will hand over, and it is nearly always
    more than was asked for, because a path runs from a headwater to its basin
    outlet *through every name change*. A 1 km query near Rich Creek fetches
    2149 reaches this way, continuing down the South Platte into Nebraska, to
    keep the 184 that are the creek in front of you.

    The GNIS id is what marks that boundary, and it is safe to use here even
    though it is not safe on its own: NHD gives the same id to unrelated
    watercourses sharing a name (gnis_id='00180229' is two Sacramento Creeks,
    570 km apart), but the level path has already pinned which one is meant.
    Path first, then name.

    That filtering happens in Python because it cannot happen in the query - the
    services refuse any where clause naming gnis_id, see RETRY_STATUS above.

    Named and unnamed reaches are NOT special-cased. There is one rule - keep
    walking while the watercourse identity is unchanged - and an unnamed reach
    simply has the identity `None`. So an unnamed headwater extends to the ends
    of its unnamed run and stops at the first named reach, which is both correct
    and the thing a filter could not have expressed: the unnamed reaches on a
    path are not one stretch, and asking the service for all of them returns
    fragments scattered along its whole length. Resist adding a branch here.

    Several seeds on one path each grow their own run, since the identity is
    read from the reach being walked rather than from any single seed. A reach
    can only ever be added by a run it matches, so the runs cannot bleed into
    each other.

    Only the main up/down links are followed. Minor divergence links
    (`dnminorhyd`) are left alone: they leave the level path by definition, so
    following them would reintroduce the sprawl this exists to prevent.
    """
    by_seq = {}
    for r in reaches:
        seq = r['properties'].get(src.seq_field)
        if seq is not None:
            by_seq[seq] = r

    kept = {seq: by_seq[seq] for seq in seeds if seq in by_seq}
    stack = list(kept)
    while stack:
        current = by_seq[stack.pop()]
        here = watercourse_of(current['properties'], src)
        for link in (src.up_field, src.dn_field):
            nxt = current['properties'].get(link)
            neighbour = by_seq.get(nxt)
            if neighbour is None or nxt in kept:
                continue
            if watercourse_of(neighbour['properties'], src) != here:
                continue
            kept[nxt] = neighbour
            stack.append(nxt)
    return list(kept.values())


def fetch(source, spatial, traverse=False, ephemeral='keep'):
    src = SOURCES[source]
    feats = []
    for layer, fields in src.layers.items():
        feats.extend(query_layer(src.base, layer, fields, spatial))

    if traverse:
        # Which reaches, on which level paths, the spatial query actually
        # touched. These seed the walk; everything else on a path is kept only
        # if the walk reaches it.
        seeds = defaultdict(set)
        for f in feats:
            props = f['properties']
            path, seq = props.get(src.path_field), props.get(src.seq_field)
            if path not in (None, '') and seq is not None:
                seeds[path].add(seq)

        if seeds:
            print(f'traversing {len(seeds)} stream path(s)...', file=sys.stderr)
            # One request per path. `IN (...)` would fetch them together and is
            # refused from Python - see RETRY_STATUS above - but a query only
            # ever touches a handful of paths, so one request each costs little.
            #
            # Each fetches the path whole and then discards most of it, which is
            # why --traverse is slow: keeping 184 reaches of the South Fork
            # South Platte means pulling all 2149 on its path first.
            for path, found in sorted(seeds.items()):
                whole = query_layer(src.base, src.flow_layer,
                                    src.layers[src.flow_layer],
                                    {'where': f'{src.path_field}={path_literal(path)}'})
                feats.extend(traverse_path(whole, found, src))

    kept, seen, dropped = [], set(), 0
    for f in feats:
        if not f.get('geometry'):
            continue
        if ephemeral == 'drop' and f['properties'].get('fcode') in EPHEMERAL_FCODES:
            dropped += 1
            continue
        key = feature_key(f)
        if key in seen:
            continue
        seen.add(key)
        kept.append(f)
    return kept, dropped


FCODE_LABEL = {
    46000: 'Stream/River', 46003: 'Stream/River Intermittent',
    46006: 'Stream/River Perennial', 46007: 'Stream/River Ephemeral',
    33600: 'Canal/Ditch', 55800: 'Artificial Path', 33400: 'Connector',
    39004: 'Lake/Pond Perennial', 39001: 'Lake/Pond Intermittent',
    43600: 'Reservoir', 46600: 'Swamp/Marsh', 45800: 'Spring/Seep',
}


def summarise(feats, dropped):
    counts = defaultdict(int)
    for f in feats:
        p = f['properties']
        label = (FCODE_LABEL.get(p.get('fcode'), f"fcode {p.get('fcode')}")
                 if 'fcode' in p else p.get('featuretypelabel', '?'))
        counts[label] += 1
    print(f'{len(feats)} features'
          + (f' ({dropped} ephemeral dropped)' if dropped else ''))
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'  {n:>5}  {label}')
    named = {f['properties'].get('gnis_name') or f['properties'].get('gnisidlabel')
             for f in feats}
    named.discard(None)
    if named:
        print(f'  {len(named)} named watercourse(s): '
              + ', '.join(sorted(named)[:6])
              + (' ...' if len(named) > 6 else ''))


# --- tagging -----------------------------------------------------------------
#
# NHDPlus HR fcode -> OSM tags. Intermittent and ephemeral reaches get
# intermittent=yes: they are real channels that are dry for part of the year,
# which is exactly what someone looking for drinking water needs to know.
#
# Waterbody Connector / Artificial Path are KEPT, not dropped. Where a stream
# threads through a pond these carry the flow line across it; dropping them
# fractures the waterway into disconnected pieces. OSM practice is to draw the
# watercourse through small waterbodies. For a genuinely large lake, delete
# them on review instead.
FCODE_TAGS = {
    46000: {'waterway': 'stream'},
    46006: {'waterway': 'stream'},
    46003: {'waterway': 'stream', 'intermittent': 'yes'},
    46007: {'waterway': 'stream', 'intermittent': 'yes'},
    55800: {'waterway': 'stream'},
    33400: {'waterway': 'stream'},
    33600: {'waterway': 'ditch'},
    33601: {'waterway': 'ditch'},
    33603: {'waterway': 'ditch'},
    42800: None,                      # pipeline - not surface water
    42000: None,                      # underground conduit
    39000: {'natural': 'water', 'water': 'pond'},
    39004: {'natural': 'water', 'water': 'pond'},
    39009: {'natural': 'water', 'water': 'pond'},
    39010: {'natural': 'water', 'water': 'pond'},
    39011: {'natural': 'water', 'water': 'pond'},
    39012: {'natural': 'water', 'water': 'pond'},
    39001: {'natural': 'water', 'water': 'pond', 'intermittent': 'yes'},
    43600: {'natural': 'water', 'water': 'reservoir'},
    43601: {'natural': 'water', 'water': 'reservoir'},
    43610: {'natural': 'water', 'water': 'reservoir'},
    43613: {'natural': 'water', 'water': 'reservoir'},
    43617: {'natural': 'water', 'water': 'reservoir'},
    46600: {'natural': 'wetland', 'wetland': 'marsh'},
    46602: {'natural': 'wetland', 'wetland': 'marsh'},
    46601: {'natural': 'wetland', 'wetland': 'marsh', 'intermittent': 'yes'},
    36100: {'natural': 'water', 'water': 'lake', 'intermittent': 'yes'},
    45800: {'natural': 'spring'},
    44500: {'natural': 'sinkhole'},
    48700: {'waterway': 'waterfall'},
    44100: {'waterway': 'rapids'},
    37800: {'natural': 'glacier'},
}

# 3DHP fallback. Note this schema has NO hydrographic category, so everything
# arrives looking perennial - do not use it to judge August water.
DHP_LINE = {'Channel Line': {'waterway': 'stream'},
            'River': {'waterway': 'river'},
            'Canal': {'waterway': 'ditch'},
            'Drainageway': {'waterway': 'stream'},
            'Waterbody Connector': {'waterway': 'stream'},
            'Surface Connector': {'waterway': 'stream'},
            'Elevation Breaching Connector': None,
            'Hydro Unenforced Connector': None}
DHP_AREA = {'Lake': {'natural': 'water', 'water': 'pond'},
            'Reservoir': {'natural': 'water', 'water': 'reservoir'},
            'Swamp/Marsh': {'natural': 'wetland', 'wetland': 'marsh'}}
DHP_POINT = {'Spring': {'natural': 'spring'}, 'Sink': {'natural': 'sinkhole'}}

# --- stream vs river ---------------------------------------------------------
#
# Neither source states it. NHDPlus lumps everything under FType 460
# "StreamRiver"; 3DHP's "River" type covers only ~35k features nationally out of
# 22M, so it means "major river", not OSM's threshold.
#
# OSM's line is "a stream can be jumped across by an active, able-bodied
# person", suggested as under 3 m - and for varying watercourses the wiki says
# to judge at HIGH water. Two signals are available:
#
#   the name   describes the whole named feature and is stable. US toponymy is
#              consistent enough to use: Creek/Brook/Run/Branch/Gulch are small,
#              River is not. Following it also keeps our output consistent with
#              how surrounding features are already tagged.
#   qama       describes THIS reach, in cfs, and varies hugely along a
#              watercourse - the South Fork South Platte runs 0.18 to 124 cfs
#              over its length. It is a modelled MEAN ANNUAL figure, so in a
#              snowmelt catchment it understates the high-water width OSM asks
#              you to judge by.
#
# The name wins where both are available; qama catches cases worth a human
# look. Flow-to-width conversion is regional and noisy, so the middle band is
# treated as inconclusive rather than faking precision - and a hard single
# threshold would split rivers arbitrarily wherever qama crossed it.
RIVER_CFS = 30.0    # 0.85 m3/s - comfortably river-width in most settings
STREAM_CFS = 10.0   # 0.28 m3/s - confidently jumpable
NAME_GENERICS = {
    'river': 'river', 'rio': 'river',
    'creek': 'stream', 'crick': 'stream', 'brook': 'stream', 'run': 'stream',
    'branch': 'stream', 'kill': 'stream', 'rill': 'stream', 'gulch': 'stream',
    'draw': 'stream', 'wash': 'stream', 'arroyo': 'stream', 'gully': 'stream',
    'coulee': 'stream', 'bayou': 'stream', 'slough': 'stream',
    'prong': 'stream', 'swale': 'stream',
}


def waterway_class(name, qama):
    """Return (waterway_value, needs_review)."""
    from_name = None
    if name:
        for word in reversed(name.replace('-', ' ').split()):
            hit = NAME_GENERICS.get(word.strip('.,()').lower())
            if hit:
                from_name = hit
                break

    from_flow = None
    if isinstance(qama, (int, float)) and qama > 0:
        from_flow = ('river' if qama >= RIVER_CFS
                     else 'stream' if qama <= STREAM_CFS else None)

    if from_name and from_flow and from_name != from_flow:
        return from_name, True          # trust the name, but say so
    return from_name or from_flow or 'stream', False


def tags_for(props, gtype):
    """Resolve OSM tags from either schema. (False, None) means unmapped."""
    if 'fcode' in props:                                    # NHDPlus HR
        fc = props.get('fcode')
        if fc not in FCODE_TAGS:
            return False, None
        if FCODE_TAGS[fc] is None:
            return None, None
        tags = dict(FCODE_TAGS[fc])
        name = props.get('gnis_name')
        if tags.get('waterway') == 'stream':       # never reclassify a ditch
            cls, review = waterway_class(name, props.get('qama'))
            tags['waterway'] = cls
            if review:
                tags['fixme'] = ('check stream vs river: '
                                 'name and modelled flow disagree')
        return tags, name

    table = (DHP_LINE if gtype in ('LineString', 'MultiLineString')
             else DHP_AREA if gtype in ('Polygon', 'MultiPolygon') else DHP_POINT)
    ftype = props.get('featuretypelabel')
    if ftype not in table:
        return False, None
    tags = table[ftype]
    return (dict(tags) if tags else None), props.get('gnisidlabel')


# --- conversion --------------------------------------------------------------

def merge_chains(ways):
    """Merge open ways meeting head-to-tail with identical tags.

    The test is ambiguity, not node degree. A tributary joining is irrelevant:
    OSM is happy for one way to run through a node where another way ends, so
    the main channel stays continuous and the tributary terminates on one of its
    intermediate nodes. What blocks a merge is not knowing WHICH way continues -
    so merge only where, among ways sharing a tag set, exactly one arrives at
    the node and exactly one leaves.

    That refuses the two undecidable cases: a divergence (one in, two out) and
    two same-named channels flowing into each other (two in, one out). Ways are
    never reversed - the source digitises in flow direction, and flipping one to
    force a join would corrupt that.
    """
    closed = [w for w in ways if w[1][0] == w[1][-1]]
    open_ = [w for w in ways if w[1][0] != w[1][-1]]

    arriving, leaving = defaultdict(list), defaultdict(list)
    for i, (tags, nds, _) in enumerate(open_):
        arriving[(tags, nds[-1])].append(i)
        leaving[(tags, nds[0])].append(i)

    nxt = {}
    for i, (tags, nds, _) in enumerate(open_):
        ins, outs = arriving[(tags, nds[-1])], leaving[(tags, nds[-1])]
        if len(ins) == 1 and len(outs) == 1 and outs[0] != i:
            nxt[i] = outs[0]

    targets = set(nxt.values())
    out = []
    for i, (tags, nds, wid) in enumerate(open_):
        if i in targets:
            continue
        chain, seen, cur = nds[:], {i}, i
        while cur in nxt:
            cur = nxt[cur]
            if cur in seen:
                break                   # defensive: refuse to loop
            seen.add(cur)
            chain.extend(open_[cur][1][1:])
        out.append((tags, chain, wid))
    return out + closed


def convert(src, dst, merge=True):
    data = json.load(open(src))
    nodes, node_defs, ways, relations = {}, [], [], []
    next_id = [-1]

    def node_id(coord):
        key = (round(coord[0], 7), round(coord[1], 7))
        if key not in nodes:
            nodes[key] = next_id[0]
            node_defs.append((next_id[0], key, {}))
            next_id[0] -= 1
        return nodes[key]

    def collect(coords, tags, closed=False):
        ids = [node_id(c) for c in coords]
        if closed and ids[0] != ids[-1]:
            ids.append(ids[0])
        deduped = [ids[0]]
        for i in ids[1:]:
            if i != deduped[-1]:
                deduped.append(i)
        if len(deduped) < 2:
            return None
        wid = next_id[0]
        next_id[0] -= 1
        ways.append((tags, deduped, wid))
        return wid

    def add_polygon(rings, tags):
        """A bare outer ring would claim any islands as water they are not, so
        anything with holes becomes a proper multipolygon relation."""
        if len(rings) == 1:
            collect(rings[0], tags, closed=True)
            return
        members = []
        for i, ring in enumerate(rings):
            wid = collect(ring, (), closed=True)   # members untagged
            if wid is not None:
                members.append((wid, 'outer' if i == 0 else 'inner'))
        if members:
            relations.append((tags, members))

    unmapped = dropped = 0
    for f in data.get('features') or []:
        geom, props = f.get('geometry'), f.get('properties') or {}
        if not geom:
            continue
        gtype = geom['type']
        tags, name = tags_for(props, gtype)
        if tags is False:
            print(f'  ! unmapped {gtype} '
                  f'{props.get("fcode", props.get("featuretypelabel"))!r}',
                  file=sys.stderr)
            unmapped += 1
            continue
        if tags is None:
            dropped += 1
            continue
        if name and name != 'null':
            tags['name'] = name
        tags = tuple(sorted(tags.items()))

        if gtype == 'LineString':
            collect(geom['coordinates'], tags)
        elif gtype == 'MultiLineString':
            for line in geom['coordinates']:
                collect(line, tags)
        elif gtype == 'Polygon':
            add_polygon(geom['coordinates'], tags)
        elif gtype == 'MultiPolygon':
            for poly in geom['coordinates']:
                add_polygon(poly, tags)
        elif gtype == 'Point':
            nid = node_id(geom['coordinates'])
            for i, (n, key, t) in enumerate(node_defs):
                if n == nid:
                    node_defs[i] = (n, key, dict(tags))
                    break

    before = len(ways)
    if merge:
        ways = merge_chains(ways)

    used = {n for _, nds, _ in ways for n in nds}
    root = ET.Element('osm', version='0.6', generator='hydrate-osm')
    for nid, (lon, lat), tags in node_defs:
        if nid not in used and not tags:
            continue
        node = ET.SubElement(root, 'node', id=str(nid), lat=str(lat),
                             lon=str(lon), version='1')
        for k, v in tags.items():
            ET.SubElement(node, 'tag', k=k, v=v)
    for tags, nds, wid in ways:
        way = ET.SubElement(root, 'way', id=str(wid), version='1')
        for n in nds:
            ET.SubElement(way, 'nd', ref=str(n))
        for k, v in tags:
            ET.SubElement(way, 'tag', k=k, v=v)
    for tags, members in relations:
        rid = next_id[0]
        next_id[0] -= 1
        rel = ET.SubElement(root, 'relation', id=str(rid), version='1')
        for wid, role in members:
            ET.SubElement(rel, 'member', type='way', ref=str(wid), role=role)
        ET.SubElement(rel, 'tag', k='type', v='multipolygon')
        for k, v in tags:
            ET.SubElement(rel, 'tag', k=k, v=v)

    ET.ElementTree(root).write(dst, encoding='utf-8', xml_declaration=True)
    flagged = sum(1 for t, _, _ in ways if any(k == 'fixme' for k, _ in t))
    print(f'{before} segments -> {len(ways)} ways'
          f'{" (merged)" if merge else ""}, {len(relations)} multipolygon(s), '
          f'{dropped} dropped, {unmapped} unmapped')
    print(f'{len(used)} nodes -> {dst}')
    if flagged:
        print(f'{flagged} way(s) tagged fixme - search `fixme` in JOSM')


# --- cli ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        prog='hydrate.py', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    q = sub.add_parser('query', help='fetch hydrography as GeoJSON')
    where = q.add_mutually_exclusive_group(required=True)
    where.add_argument('--at', metavar='LAT,LON', help='a point')
    where.add_argument('--track', metavar='FILE.gpx', help='a GPX track or route')
    where.add_argument('--huc', metavar='HUC12', help='a whole subwatershed')
    q.add_argument('--radius', type=float, default=1000,
                   help='metres from the point or track (default 1000)')
    q.add_argument('--traverse', action='store_true',
                   help='extend each reach found to its whole stream path')
    q.add_argument('--ephemeral', choices=('keep', 'drop'),
                   help='reaches that only run after rain '
                        '(default: keep for --at/--track, drop for --huc)')
    q.add_argument('--source', choices=tuple(SOURCES), default='nhdplus')
    q.add_argument('-o', '--out', metavar='FILE.geojson', required=True)

    h = sub.add_parser('huc-at', help='which HUC12 subwatershed contains a point')
    h.add_argument('at', metavar='LAT,LON')

    c = sub.add_parser('convert', help='GeoJSON -> JOSM-editable .osm')
    c.add_argument('src', metavar='in.geojson')
    c.add_argument('dst', metavar='out.osm')
    c.add_argument('--no-merge', action='store_true',
                   help='keep source reach segmentation instead of merging')

    a = ap.parse_args()

    if a.cmd == 'huc-at':
        lat, lon = (float(x) for x in a.at.split(','))
        for h in huc_at(lat, lon):
            print(f"{h['huc12']}  {h['name']}  ({h['areasqkm']:.1f} km2)")
        return

    if a.cmd == 'convert':
        convert(a.src, a.dst, merge=not a.no_merge)
        return

    ephemeral = a.ephemeral or ('drop' if a.huc else 'keep')
    if a.at:
        lat, lon = (float(x) for x in a.at.split(','))
        spatial = around_point(lat, lon, a.radius)
        print(f'within {a.radius:.0f} m of {lat},{lon}')
    elif a.track:
        pts, raw = read_gpx(a.track, min_spacing=a.radius / 2)
        spatial = along_track(pts, a.radius)
        print(f'within {a.radius:.0f} m of {a.track} '
              f'({raw} points thinned to {len(pts)})')
    else:
        spatial = within_huc(a.huc)
        print(f'inside HUC12 {a.huc}')

    feats, dropped = fetch(a.source, spatial, a.traverse, ephemeral)
    summarise(feats, dropped)
    with open(a.out, 'w') as fh:
        json.dump({'type': 'FeatureCollection', 'features': feats}, fh)
    print(f'-> {a.out}')


if __name__ == '__main__':
    main()

