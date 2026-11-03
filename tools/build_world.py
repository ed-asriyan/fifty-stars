"""Build data/world.json for the World tab.

Input: Natural Earth 1:50m admin-0 countries and tiny-country points (GeoJSON), from
https://github.com/nvkelso/natural-earth-vector (public domain).

Output: a TopoJSON topology whose coordinates are already projected (Natural Earth I) into
an SVG viewBox, so the page draws it with d3.geo.path().projection(null). Shared borders are
stored once as arcs and simplified once, so neighbouring countries never get slivers.
Extra metadata for the page lives under "fifty".

Usage: python3 tools/build_world.py <ne_50m_admin_0_countries.geojson> <ne_50m_admin_0_tiny_countries.geojson> data/world.json
"""
import json, math, sys
from collections import defaultdict

SRC, TINY, OUT = sys.argv[1:4]

W = 1000.0          # viewBox width
PAD = 6.0           # viewBox padding
SOUTH = -57.0       # crop: everything south of this latitude is dropped (Antarctica)
GRID = 0.02         # quantization step in viewBox units
TOL = 0.2           # Douglas-Peucker tolerance in viewBox units
MIN_ISLAND = 0.03   # drop extra polygons smaller than this (viewBox units^2)

# 193 UN member states plus the two observer states (Vatican, Palestine).
UN = set("""AF AL DZ AD AO AG AR AM AU AT AZ BS BH BD BB BY BE BZ BJ BT BO BA BW BR BN BG BF BI CV KH CM CA CF TD
CL CN CO KM CG CD CR CI HR CU CY CZ DK DJ DM DO EC EG SV GQ ER EE SZ ET FJ FI FR GA GM GE DE GH GR GD GT GN GW
GY HT HN HU IS IN ID IR IQ IE IL IT JM JP JO KZ KE KI KP KR KW KG LA LV LB LS LR LY LI LT LU MG MW MY MV ML MT
MH MR MU MX FM MD MC MN ME MA MZ MM NA NR NP NL NZ NI NE NG MK NO OM PK PW PA PG PY PE PH PL PT QA RO RU RW KN
LC VC WS SM ST SA SN RS SC SL SG SK SI SB SO ZA SS ES LK SD SR SE CH SY TJ TZ TH TL TG TO TT TN TR TM TV UG UA
AE GB US UY UZ VU VE VN YE ZM ZW""".split())
OBSERVERS = {"VA", "PS"}
assert len(UN) == 193, len(UN)

# Short, familiar English names where Natural Earth's NAME_EN reads oddly.
NAMES = {
    "US": "United States", "CZ": "Czechia", "TL": "Timor-Leste", "CI": "Côte d'Ivoire", "CV": "Cabo Verde",
    "VA": "Vatican City", "BS": "Bahamas", "GM": "Gambia", "FM": "Micronesia", "CD": "DR Congo",
    "CG": "Congo", "MK": "North Macedonia", "SZ": "Eswatini", "PS": "Palestine", "KR": "South Korea",
    "KP": "North Korea", "MM": "Myanmar", "VC": "St Vincent & the Grenadines", "KN": "St Kitts & Nevis",
    "LC": "Saint Lucia", "ST": "São Tomé & Príncipe", "AG": "Antigua & Barbuda", "BA": "Bosnia & Herzegovina",
    "TT": "Trinidad & Tobago", "HK": "Hong Kong", "MO": "Macau", "EH": "Western Sahara",
    "CN": "China", "GS": "South Georgia & South Sandwich Is.", "HM": "Heard & McDonald Islands",
    "TF": "French Southern Lands", "VI": "US Virgin Islands", "PM": "St Pierre & Miquelon", "TC": "Turks & Caicos Islands",
}
# Pieces Natural Earth keeps separate that belong to a listed country.
MERGE = {"SOL": "SO", "CYN": "CY"}
SKIP_A3 = {"ATA", "KAS"}  # Antarctica, Siachen Glacier


def ne1(lon, lat):
    """Natural Earth I projection (Šavrič, Patterson, Jenny), radians in, plane units out."""
    l, p = math.radians(lon), math.radians(lat)
    p2 = p * p
    p4 = p2 * p2
    x = l * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)))
    y = p * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)))
    return x, y


XMAX = ne1(180, 0)[0]
S = (W - 2 * PAD) / (2 * XMAX)
YTOP = ne1(0, 84)[1]
YBOT = ne1(0, SOUTH)[1]
H = round((YTOP - YBOT) * S + 2 * PAD, 1)


def proj(lon, lat):
    x, y = ne1(lon, max(lat, SOUTH))
    return ((x + XMAX) * S + PAD, (YTOP - y) * S + PAD)


def q(pt):
    return (round(pt[0] / GRID), round(pt[1] / GRID))


def ring_area(r):  # shoelace, grid units -> viewBox units^2
    a = 0
    for i in range(len(r) - 1):
        a += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
    return a / 2 * GRID * GRID


def ring_centroid(r):
    a = cx = cy = 0
    for i in range(len(r) - 1):
        c = r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
        a += c
        cx += (r[i][0] + r[i + 1][0]) * c
        cy += (r[i][1] + r[i + 1][1]) * c
    if a == 0:
        return r[0][0] * GRID, r[0][1] * GRID
    return cx / (3 * a) * GRID, cy / (3 * a) * GRID


def clean_ring(coords):
    out = []
    for lon, lat in coords:
        p = q(proj(lon, lat))
        if not out or out[-1] != p:
            out.append(p)
    if out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def polygons_of(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


# ---------- read countries ----------
src = json.load(open(SRC))["features"]
countries = {}  # iso2 -> dict
for f in src:
    p = f["properties"]
    if p["ADM0_A3"] in SKIP_A3:
        continue
    iso = MERGE.get(p["ADM0_A3"]) or p["ISO_A2_EH"]
    if not iso or iso == "-99" or len(iso) != 2:
        print("skip (no code):", p["NAME"], p["ADM0_A3"], file=sys.stderr)
        continue
    c = countries.setdefault(iso, {"id": iso, "polys": [], "props": None})
    if c["props"] is None or p["ADM0_A3"] not in MERGE:
        if p["ADM0_A3"] not in MERGE and (c["props"] is None or p["TYPE"] in ("Sovereign country", "Country")):
            c["props"] = p
    for poly in polygons_of(f["geometry"]):
        if poly and min(lat for _, lat in poly[0]) >= SOUTH - 0.0 or max(lat for _, lat in poly[0]) > SOUTH:
            rings = [clean_ring(r) for r in poly]
            if rings[0] is None:
                continue
            c["polys"].append([r for r in rings if r])

tiny = {}
for f in json.load(open(TINY))["features"]:
    p = f["properties"]
    iso = p.get("ISO_A2")
    if not iso or len(iso) != 2:
        continue
    lon, lat = f["geometry"]["coordinates"]
    tiny[iso] = {"pt": proj(lon, lat), "props": p}

for iso, t in tiny.items():
    if iso not in countries:
        countries[iso] = {"id": iso, "polys": [], "props": t["props"]}

# drop specks that would only add noise, but never a country's only polygon
for c in countries.values():
    if not c["polys"]:
        continue
    c["polys"].sort(key=lambda poly: -abs(ring_area(poly[0])))
    c["polys"] = [poly for i, poly in enumerate(c["polys"]) if i == 0 or abs(ring_area(poly[0])) >= MIN_ISLAND]

# ---------- topology: cut rings at junctions, share arcs ----------
rings = []  # (iso, poly index, ring index, points)
for iso, c in countries.items():
    for pi, poly in enumerate(c["polys"]):
        for ri, r in enumerate(poly):
            rings.append((iso, pi, ri, r))

def ekey(a, b):
    return (a, b) if a < b else (b, a)

edge_rings = defaultdict(set)
for idx, (_, _, _, r) in enumerate(rings):
    for i in range(len(r) - 1):
        edge_rings[ekey(r[i], r[i + 1])].add(idx)

junctions = set()
for idx, (_, _, _, r) in enumerate(rings):
    n = len(r) - 1
    for i in range(n):
        prev, cur, nxt = r[i - 1], r[i], r[i + 1]
        if edge_rings[ekey(prev, cur)] != edge_rings[ekey(cur, nxt)]:
            junctions.add(cur)

arcs, arc_index = [], {}

def add_arc(pts):
    t = tuple(pts)
    if t in arc_index:
        return arc_index[t]
    rt = tuple(reversed(pts))
    if rt in arc_index:
        return ~arc_index[rt]
    arc_index[t] = len(arcs)
    arcs.append(list(pts))
    return arc_index[t]

ring_arcs = {}
for idx, (iso, pi, ri, r) in enumerate(rings):
    o = r[:-1]
    cuts = [i for i, p in enumerate(o) if p in junctions]
    if not cuts:
        s = min(range(len(o)), key=lambda i: o[i])
        o = o[s:] + o[:s]
        ring_arcs[idx] = [add_arc(o + [o[0]])]
        continue
    o = o[cuts[0]:] + o[:cuts[0]]
    cuts = [i for i, p in enumerate(o) if p in junctions] + [len(o)]
    o = o + [o[0]]
    parts = []
    for a, b in zip(cuts, cuts[1:]):
        parts.append(add_arc(o[a:b + 1]))
    ring_arcs[idx] = parts

# ---------- simplify each arc once ----------
def dp(pts, tol):
    if len(pts) <= 2:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    t2 = (tol / GRID) ** 2
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        best, bi = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, bi = d, i
        if best > t2:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]

simple = []
for pts in arcs:
    if pts[0] == pts[-1] and len(pts) > 4:  # closed arc: split at the farthest point
        far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
        s = dp(pts[:far + 1], TOL)[:-1] + dp(pts[far:], TOL)
        if len(s) < 4:
            n = len(pts) - 1
            s = [pts[0], pts[n // 3], pts[2 * n // 3], pts[0]]
    else:
        s = dp(pts, TOL)
    simple.append(s)

def delta(pts):
    out, px, py = [], 0, 0
    for x, y in pts:
        out.append([x - px, y - py])
        px, py = x, y
    return out

# ---------- geometries and metadata ----------
geoms, meta = [], []
by_country = defaultdict(lambda: defaultdict(list))
for idx, (iso, pi, ri, r) in enumerate(rings):
    by_country[iso][pi].append(ring_arcs[idx])

def region_of(p):
    r = (p or {}).get("REGION_UN") or ""
    return r if r in ("Africa", "Americas", "Asia", "Europe", "Oceania") else "Other"

xs = []
for iso in sorted(countries):
    c = countries[iso]
    p = c["props"] or {}
    name = NAMES.get(iso) or p.get("NAME_EN") or p.get("NAME") or iso
    a3 = p.get("ISO_A3_EH") if p.get("ISO_A3_EH") not in (None, "-99") else p.get("ADM0_A3", iso)
    if iso == "XK":
        a3 = "XKX"
    m = {"id": iso, "n": name, "a3": a3, "r": region_of(p), "un": 1 if iso in UN or iso in OBSERVERS else 0}
    if c["polys"]:
        polys = by_country[iso]
        arcs_poly = [polys[pi] for pi in sorted(polys)]
        geoms.append({"type": "MultiPolygon", "id": iso, "arcs": arcs_poly} if len(arcs_poly) > 1
                     else {"type": "Polygon", "id": iso, "arcs": arcs_poly[0]})
        main = c["polys"][0][0]
        m["c"] = [round(v, 1) for v in ring_centroid(main)]
        m["ar"] = round(sum(abs(ring_area(poly[0])) for poly in c["polys"]), 2)
    else:
        m["ar"] = 0
    if iso in tiny:
        m["pt"] = [round(v, 1) for v in tiny[iso]["pt"]]
        if "c" not in m:
            m["c"] = m["pt"]
    if "c" not in m:
        print("no geometry or point:", iso, name, file=sys.stderr)
        continue
    meta.append(m)

missing_un = sorted((UN | OBSERVERS) - {m["id"] for m in meta})
assert not missing_un, missing_un

topo = {
    "type": "Topology",
    "transform": {"scale": [GRID, GRID], "translate": [0, 0]},
    "objects": {"countries": {"type": "GeometryCollection", "geometries": geoms}},
    "arcs": [delta(a) for a in simple],
    "fifty": {"W": W, "H": H, "projection": "Natural Earth I", "source": "Natural Earth 1:50m admin-0 countries",
              "countries": meta},
}
json.dump(topo, open(OUT, "w"), separators=(",", ":"), ensure_ascii=False)
un_count = sum(m["un"] for m in meta)
print(f"{len(meta)} places ({un_count} UN members and observers), {len(geoms)} with shapes, "
      f"{len(arcs)} arcs, {sum(len(a) for a in simple)} points, viewBox {W:g}x{H:g}", file=sys.stderr)
