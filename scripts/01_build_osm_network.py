"""Geofabrik 한국 OSM 추출본에서 서울(+1km 버퍼) 보행 네트워크·지하철 출입구·녹지 폴리곤을 만든다.

공용 Overpass 서버 대신 고정 시점 스냅샷(.osm.pbf)을 쓰므로 결과가 재현 가능하다.
출처: © OpenStreetMap contributors (ODbL 1.0), Geofabrik 추출본 south-korea-261019
"""
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmium
import osmnx as ox
from shapely import wkb
from shapely.geometry import Point
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PBF = RAW / "osm" / "south-korea-261019.osm.pbf"
SUB = RAW / "osm" / "seoul_buffer.osm.pbf"
OUT = ROOT / "data" / "interim"
OUT.mkdir(parents=True, exist_ok=True)

# osmnx 'walk' network_type과 같은 기준 (osmnx/_overpass.py 참고)
EXCLUDE_HW = {"abandoned", "bus_guideway", "construction", "cycleway", "motor", "no", "planned",
              "platform", "proposed", "raceway", "razed", "motorway", "motorway_link"}
GREEN = {("leisure", "park"), ("landuse", "forest"), ("natural", "wood"), ("leisure", "nature_reserve"),
         ("landuse", "cemetery"), ("natural", "scrub"), ("leisure", "golf_course")}


def is_walkable(tags):
    hw = tags.get("highway")
    if hw is None or hw in EXCLUDE_HW or tags.get("area") == "yes":
        return False
    if tags.get("foot") == "no" or tags.get("service") == "private":
        return False
    if tags.get("access") == "private" and tags.get("foot") not in ("yes", "designated", "permissive"):
        return False
    return True


def seoul_area():
    dong = gpd.read_file(RAW / "boundary" / "HangJeongDong_ver20261001.geojson")
    seoul = dong[dong["sidonm"] == "서울특별시"].dissolve().geometry.iloc[0]
    return gpd.GeoSeries([seoul], crs=4326).to_crs(5179).buffer(1000).to_crs(4326).iloc[0]


def extract_subset(area):
    """전국 pbf에서 서울 버퍼 bbox에 걸치는 객체만 남긴 작은 pbf를 만든다."""
    if SUB.exists():
        return
    minx, miny, maxx, maxy = area.bounds
    # 1차: bbox 안 노드 → 그 노드를 쓰는 way → way가 참조하는 노드 전체
    node_ids, way_ids = set(), set()
    for o in osmium.FileProcessor(str(PBF), osmium.osm.NODE):
        if minx <= o.location.lon <= maxx and miny <= o.location.lat <= maxy:
            node_ids.add(o.id)
    for o in osmium.FileProcessor(str(PBF), osmium.osm.WAY):
        if any(n.ref in node_ids for n in o.nodes):
            way_ids.add(o.id)
    rel_ids, extra_ways = set(), set()
    for o in osmium.FileProcessor(str(PBF), osmium.osm.RELATION):
        if o.tags.get("type") == "multipolygon" and any(
                m.type == "w" and m.ref in way_ids for m in o.members):
            rel_ids.add(o.id)
            extra_ways.update(m.ref for m in o.members if m.type == "w")
    way_ids |= extra_ways
    need_nodes = set(node_ids)
    for o in osmium.FileProcessor(str(PBF), osmium.osm.WAY):
        if o.id in way_ids:
            need_nodes.update(n.ref for n in o.nodes)
    writer = osmium.SimpleWriter(str(SUB))
    for o in osmium.FileProcessor(str(PBF)):
        if (o.is_node() and o.id in need_nodes) or (o.is_way() and o.id in way_ids) or (
                o.is_relation() and o.id in rel_ids):
            writer.add(o)
    writer.close()


def build_walk_graph(area):
    prepared = prep(area)
    G = nx.MultiDiGraph(crs="epsg:4326")
    fp = osmium.FileProcessor(str(SUB), osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for w in fp:
        if not w.is_way():
            continue
        tags = dict(w.tags)
        if not is_walkable(tags):
            continue
        pts = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(pts) < 2 or not any(prepared.contains(Point(x, y)) for _, x, y in pts):
            continue
        attrs = {k: tags[k] for k in ("highway", "name", "bridge", "tunnel", "incline", "surface",
                                      "sidewalk", "footway", "layer") if k in tags}
        for nid, x, y in pts:
            if nid not in G:
                G.add_node(nid, x=x, y=y)
        for (u, *_), (v, *_) in zip(pts[:-1], pts[1:]):
            if u == v:
                continue
            G.add_edge(u, v, osmid=w.id, oneway=False, reversed=False, **attrs)
            G.add_edge(v, u, osmid=w.id, oneway=False, reversed=True, **attrs)
    G = ox.distance.add_edge_lengths(G)
    G = ox.simplify_graph(G)
    G = ox.truncate.largest_component(G, strongly=False)
    return G


def extract_points_and_green(area):
    prepared = prep(area)
    ents, greens = [], []
    for o in osmium.FileProcessor(str(SUB), osmium.osm.NODE):
        if o.tags.get("railway") == "subway_entrance" and prepared.contains(Point(o.location.lon, o.location.lat)):
            ents.append({"osmid": o.id, "name": o.tags.get("name"), "ref": o.tags.get("ref"),
                         "wheelchair": o.tags.get("wheelchair"),
                         "geometry": Point(o.location.lon, o.location.lat)})
    fab = osmium.geom.WKBFactory()
    for a in osmium.FileProcessor(str(SUB)).with_areas().with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)):
        if any(a.tags.get(k) == v for k, v in GREEN):
            try:
                greens.append({"kind": next(f"{k}={v}" for k, v in GREEN if a.tags.get(k) == v),
                               "geometry": wkb.loads(fab.create_multipolygon(a), hex=True)})
            except Exception:
                pass
    ent = gpd.GeoDataFrame(ents, crs=4326)
    green = gpd.GeoDataFrame(greens, crs=4326)
    green = green[green.intersects(area)]
    return ent, green


def main():
    area = seoul_area()
    print("subset pbf ...", flush=True)
    extract_subset(area)
    print("walk graph ...", flush=True)
    G = build_walk_graph(area)
    print(f"{G.number_of_nodes():,} nodes / {G.number_of_edges():,} edges", flush=True)
    ox.save_graphml(G, OUT / "seoul_walk.graphml")
    print("entrances & green ...", flush=True)
    ent, green = extract_points_and_green(area)
    ent.to_file(OUT / "subway_entrances_osm.geojson", driver="GeoJSON")
    green.to_file(OUT / "green_areas_osm.geojson", driver="GeoJSON")
    print(f"{len(ent)} entrances, {len(green)} green polygons", flush=True)


if __name__ == "__main__":
    main()
