"""경사 반영 보행 접근성 계산 (서울 전역).

1) FABDEM(건물·수목 제거 30m DEM)을 EPSG:5179로 재투영하고 보행 링크를 따라 15m 간격으로 표고를 샘플링
2) 링크(방향별) 보행시간을 시나리오별로 계산
     A 성인·평지     v0=1.33m/s, 경사 무시                 (거리 기반 관점)
     B 성인·경사     v0=1.33m/s, Tobler 경사 보정
     C 고령자·평지   v0=0.70m/s, 경사 무시
     D 고령자·경사   v0=0.70m/s, Tobler 경사 보정 + 계단 감속
     E 무장애 경로   D와 같되 계단·경사 1/12(8.33%) 초과 링크 통행 불가, 지하철은 엘리베이터로만 진입
3) 모든 정류장을 다중 출발점으로 하는 다익스트라로 각 지점의
   '정류장까지 가는 시간(t_)'과 '정류장에서 돌아오는 시간(tb_)'을 구한다. 오르막/내리막이 방향별로 다르다.
4) 지표
     숨은 소외(왕복)  : C 왕복 ≤ 2T 이지만 D 왕복 > 2T  — 경사·계단 때문에만 생기는 소외
     무장애 단절      : C 편도 ≤ T 이지만 E 편도 > T   — 기준 경사를 지키는 길로는 정류장에 못 가는 지점
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
import shapely
from rasterio.merge import merge
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

ROOT = Path(__file__).resolve().parents[1]
RAW, INT, OUT = ROOT / "data" / "raw", ROOT / "data" / "interim", ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)
CRS = 5179

SAMPLE_STEP = 15.0        # m, 링크 위 표고 샘플 간격
GRADE_CLIP = 0.40         # DEM 잡음으로 인한 비현실적 경사 제한
SNAP_MAX = 100.0          # m, 정류장-네트워크 최대 연결거리
STEPS_FACTOR_ELDER = 0.6  # 고령자 계단 구간 속도 배율 (가정)
ELDER_V = 0.70            # m/s, 경찰청 교통신호기 설치·관리 매뉴얼(2020.11.30 개정) 교통약자 보행속도 기준
BF_MAX_GRADE = 1 / 12     # 장애인등편의법 시행규칙 [별표 1] 제12호 나목: 경사로 기울기 1/12 이하
BF_SENS = {"g10": 0.10, "g125": 0.125}  # 경사 기준 민감도 분석
THRESHOLDS_MIN = [5, 8, 10, 15]
SCENARIOS = {
    "A_adult_flat": dict(v0=1.33, slope=False, steps=1.0),
    "B_adult_slope": dict(v0=1.33, slope=True, steps=1.0),
    "C_elder_flat": dict(v0=ELDER_V, slope=False, steps=1.0),
    "D_elder_slope": dict(v0=ELDER_V, slope=True, steps=STEPS_FACTOR_ELDER),
}
ROUND_TRIP = ["C_elder_flat", "D_elder_slope"]
TRAIL = {"path", "track", "bridleway"}


def tobler_factor(s):
    """Tobler(1993) 보행속도 함수를 평지=1로 정규화. 내리막 약 5%에서 최대."""
    return np.exp(-3.5 * (np.abs(s + 0.05) - 0.05))


def load_dem():
    srcs = [rasterio.open(t) for t in sorted((RAW / "fabdem").glob("*_FABDEM_V1-2.tif"))]
    arr, tr = merge(srcs)
    h, w = arr.shape[1:]
    left, bottom, right, top = rasterio.transform.array_bounds(h, w, tr)
    dst_tr, dw, dh = calculate_default_transform(srcs[0].crs, CRS, w, h, left, bottom, right, top, resolution=30)
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(arr[0], dst, src_transform=tr, src_crs=srcs[0].crs, dst_transform=dst_tr, dst_crs=CRS,
              resampling=Resampling.bilinear, src_nodata=srcs[0].nodata, dst_nodata=np.nan)
    return dst, dst_tr


def bilinear(dem, tr, x, y):
    c, r = ~tr * (x, y)
    c, r = np.asarray(c) - 0.5, np.asarray(r) - 0.5
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    dc, dr = c - c0, r - r0
    c0 = np.clip(c0, 0, dem.shape[1] - 2)
    r0 = np.clip(r0, 0, dem.shape[0] - 2)
    return (dem[r0, c0] * (1 - dc) * (1 - dr) + dem[r0, c0 + 1] * dc * (1 - dr)
            + dem[r0 + 1, c0] * (1 - dc) * dr + dem[r0 + 1, c0 + 1] * dc * dr)


def first_str(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def edge_table(G):
    nodes, edges = ox.graph_to_gdfs(G)
    edges = edges.reset_index()
    # 링크 geometry가 u에서 시작하도록 방향 정렬
    upts = shapely.points(nodes.loc[edges["u"], "x"].to_numpy(), nodes.loc[edges["u"], "y"].to_numpy())
    g = np.asarray(edges.geometry.values)
    flip = shapely.distance(shapely.get_point(g, -1), upts) < shapely.distance(shapely.get_point(g, 0), upts)
    g[flip] = shapely.reverse(g[flip])
    edges["geometry"] = g
    for c in ("highway", "bridge", "tunnel"):
        edges[c] = edges[c].map(first_str) if c in edges else None
    return nodes, edges


def segment_grades(edges, dem, tr):
    L = edges["length"].to_numpy()
    nseg = np.maximum(1, np.ceil(L / SAMPLE_STEP).astype(int))
    idx = np.repeat(np.arange(len(edges)), nseg + 1)
    starts = np.concatenate([[0], np.cumsum(nseg + 1)[:-1]])
    frac = (np.arange(len(idx)) - starts[idx]) / nseg[idx]
    pts = shapely.line_interpolate_point(np.asarray(edges.geometry.values)[idx], frac, normalized=True)
    z = bilinear(dem, tr, shapely.get_x(pts), shapely.get_y(pts))
    seg_edge = np.repeat(np.arange(len(edges)), nseg)
    seg_starts = np.concatenate([[0], np.cumsum(nseg)[:-1]])
    p0 = starts[seg_edge] + (np.arange(len(seg_edge)) - seg_starts[seg_edge])
    seg_len = (L / nseg)[seg_edge]
    grade = np.clip((z[p0 + 1] - z[p0]) / np.maximum(seg_len, 1.0), -GRADE_CLIP, GRADE_CLIP)
    structure = (edges["bridge"].fillna("no").ne("no") | edges["tunnel"].fillna("no").ne("no")).to_numpy()
    grade[structure[seg_edge]] = 0.0  # 교량·터널은 지형 표고와 무관
    return seg_edge, seg_len, np.nan_to_num(grade)


def edge_times(edges, seg_edge, seg_len, grade):
    is_steps = edges["highway"].eq("steps").to_numpy()
    out = {}
    for name, p in SCENARIOS.items():
        f = tobler_factor(grade) if p["slope"] else np.ones_like(grade)
        t = np.bincount(seg_edge, weights=seg_len / (p["v0"] * f), minlength=len(edges))
        t[is_steps] /= p["steps"]
        out[name] = np.maximum(t, 1e-3)
    return out


def nearest_times(n, u_idx, v_idx, w, src_idx, src_offset, back=False):
    """가상 출발점(index n)에서 모든 정류장으로 연결한 뒤 단일 다익스트라.
    back=False: 각 노드 → 가장 가까운 정류장 (역방향 그래프)
    back=True : 가장 가까운 정류장 → 각 노드 (정방향 그래프, 귀갓길)"""
    ok = np.isfinite(w)
    a, b, w = (u_idx[ok], v_idx[ok], w[ok]) if back else (v_idx[ok], u_idx[ok], w[ok])
    e = pd.DataFrame({"a": a, "b": b, "w": w}).groupby(["a", "b"], as_index=False)["w"].min()
    src = pd.Series(np.maximum(src_offset, 1e-3)).groupby(np.asarray(src_idx)).min()
    rows = np.concatenate([e["a"].to_numpy(), np.full(len(src), n)])
    cols = np.concatenate([e["b"].to_numpy(), src.index.to_numpy()])
    data = np.concatenate([e["w"].to_numpy(), src.to_numpy()])
    M = csr_matrix((data, (rows, cols)), shape=(n + 1, n + 1))
    return dijkstra(M, directed=True, indices=n)[:-1]


def main():
    G = ox.project_graph(ox.load_graphml(INT / "seoul_walk.graphml"), to_crs=CRS)
    nodes, edges = edge_table(G)
    print(f"nodes {len(nodes):,}, directed edges {len(edges):,}", flush=True)

    dem, tr = load_dem()
    nodes["elev"] = bilinear(dem, tr, nodes["x"].to_numpy(), nodes["y"].to_numpy())
    seg_edge, seg_len, grade = segment_grades(edges, dem, tr)
    times = edge_times(edges, seg_edge, seg_len, grade)
    L = np.maximum(edges["length"].to_numpy(), 1e-6)
    edges["abs_grade"] = np.bincount(seg_edge, weights=np.abs(grade) * seg_len, minlength=len(edges)) / L
    edges["net_grade"] = np.bincount(seg_edge, weights=grade * seg_len, minlength=len(edges)) / L
    for k, t in times.items():
        edges[f"t_{k}"] = t
    barrier = (edges["abs_grade"] > BF_MAX_GRADE) | edges["highway"].eq("steps")
    edges["t_E_barrier_free"] = np.where(barrier, np.inf, edges["t_D_elder_slope"])
    edges["barrier"] = barrier

    stops = gpd.read_file(INT / "transit_stops.gpkg").to_crs(CRS)
    stops["node"], stops["snap_m"] = ox.distance.nearest_nodes(
        G, stops.geometry.x.to_numpy(), stops.geometry.y.to_numpy(), return_dist=True)
    stops = stops[stops["snap_m"] <= SNAP_MAX]
    print(stops.groupby("kind").size().to_dict(), "stops snapped", flush=True)

    pos = pd.Series(np.arange(len(nodes)), index=nodes.index)
    u_idx, v_idx = pos.loc[edges["u"]].to_numpy(), pos.loc[edges["v"]].to_numpy()
    n = len(nodes)
    std = stops[stops["kind"].isin(["bus", "subway"])]   # 일반 시나리오: 버스 + 지하철 출입구
    bf = stops[stops["kind"].isin(["bus", "subway_elev"])]  # 무장애: 버스 + 지하철 엘리베이터

    res = pd.DataFrame(index=nodes.index)
    for k, p in SCENARIOS.items():
        w = edges[f"t_{k}"].to_numpy()
        args = (n, u_idx, v_idx, w, pos.loc[std["node"]].to_numpy(), std["snap_m"].to_numpy() / p["v0"])
        res[f"t_{k}"] = nearest_times(*args)
        if k in ROUND_TRIP:
            res[f"tb_{k}"] = nearest_times(*args, back=True)
    res["t_E_barrier_free"] = nearest_times(n, u_idx, v_idx, edges["t_E_barrier_free"].to_numpy(),
                                            pos.loc[bf["node"]].to_numpy(), bf["snap_m"].to_numpy() / ELDER_V)
    for tag, thr in BF_SENS.items():
        bar = (edges["abs_grade"] > thr) | edges["highway"].eq("steps")
        w = np.where(bar, np.inf, edges["t_D_elder_slope"])
        res[f"t_E_{tag}"] = nearest_times(n, u_idx, v_idx, w, pos.loc[bf["node"]].to_numpy(),
                                          bf["snap_m"].to_numpy() / ELDER_V)
    res["dist_m"] = nearest_times(n, u_idx, v_idx, edges["length"].to_numpy(),
                                  pos.loc[std["node"]].to_numpy(), std["snap_m"].to_numpy())

    # 출발지(거주 가능 지점): 서울 행정동 내부, 녹지 밖, 산책로 전용 노드 제외
    ng = gpd.GeoDataFrame(res.join(nodes[["x", "y", "elev"]]),
                          geometry=gpd.points_from_xy(nodes["x"], nodes["y"]), crs=CRS)
    dong = gpd.read_file(INT / "seoul_dong.gpkg").to_crs(CRS)
    ng = gpd.sjoin(ng, dong[["adm_cd2", "sggnm", "dong", "geometry"]], how="inner", predicate="within")
    ng = ng.drop(columns="index_right")
    ng = ng[~ng.index.duplicated()]  # 동 경계선 위 노드는 한 동에만 배정
    green = gpd.read_file(INT / "green_areas_osm.geojson").to_crs(CRS)
    in_green = gpd.sjoin(ng[["geometry"]], green[["geometry"]], how="inner", predicate="within").index.unique()
    non_trail = edges.loc[~edges["highway"].isin(TRAIL), "u"].unique()
    ng["origin"] = ~ng.index.isin(in_green) & ng.index.isin(non_trail)
    ng["local_grade"] = edges.groupby("u")["abs_grade"].mean().reindex(ng.index).to_numpy()

    ng["rt_C"] = ng["t_C_elder_flat"] + ng["tb_C_elder_flat"]
    ng["rt_D"] = ng["t_D_elder_slope"] + ng["tb_D_elder_slope"]
    for T in THRESHOLDS_MIN:
        s = T * 60
        ng[f"hidden_{T}"] = (ng["rt_C"] <= 2 * s) & (ng["rt_D"] > 2 * s)
        ng[f"visible_{T}"] = ng["rt_C"] > 2 * s
        ng[f"bfgap_{T}"] = (ng["t_C_elder_flat"] <= s) & (ng["t_E_barrier_free"] > s)
    for tag in BF_SENS:
        ng[f"bfgap_{tag}_10"] = (ng["t_C_elder_flat"] <= 600) & (ng[f"t_E_{tag}"] > 600)
    ng["rt_extra_min"] = (ng["rt_D"] - ng["rt_C"]) / 60
    ng["uphill_home_min"] = (ng["tb_D_elder_slope"] - ng["t_D_elder_slope"]) / 60  # 귀갓길이 더 걸리는 시간
    ng["bf_unreachable"] = ~np.isfinite(ng["t_E_barrier_free"])

    ng.to_parquet(OUT / "node_access.parquet")
    keep = ["u", "v", "key", "length", "highway", "abs_grade", "net_grade", "barrier", "geometry"] + \
        [f"t_{k}" for k in SCENARIOS]
    edges[keep].to_parquet(OUT / "edges_grade.parquet")

    # 주거지 보행망의 급경사(>1/12) 비율 — 구 선정 지표 (산책로·녹지 제외)
    origin_ids = ng.index[ng["origin"]]
    res_e = edges[edges["u"].isin(origin_ids) & ~edges["highway"].isin(TRAIL)].copy()
    res_e = res_e.join(ng["sggnm"], on="u")
    steep = res_e.assign(sl=res_e["length"] * (res_e["abs_grade"] > BF_MAX_GRADE)) \
        .groupby("sggnm")[["sl", "length"]].sum()
    steep_share = (steep["sl"] / steep["length"]).rename("steep_share")

    o = ng[ng["origin"]]
    agg = o.groupby("adm_cd2").agg(
        n_origin=("origin", "size"), mean_local_grade=("local_grade", "mean"),
        t_A_med=("t_A_adult_flat", "median"), rt_C_med=("rt_C", "median"), rt_D_med=("rt_D", "median"),
        rt_extra_min=("rt_extra_min", "mean"), uphill_home_min=("uphill_home_min", "mean"),
        bf_unreachable_share=("bf_unreachable", "mean"),
        **{f"hidden_share_{T}": (f"hidden_{T}", "mean") for T in THRESHOLDS_MIN},
        **{f"visible_share_{T}": (f"visible_{T}", "mean") for T in THRESHOLDS_MIN},
        **{f"bfgap_share_{T}": (f"bfgap_{T}", "mean") for T in THRESHOLDS_MIN},
        **{f"bfgap_{tag}_share_10": (f"bfgap_{tag}_10", "mean") for tag in BF_SENS},
    ).reset_index()
    steep_d = res_e.assign(sl=res_e["length"] * (res_e["abs_grade"] > BF_MAX_GRADE)).join(
        ng["adm_cd2"], on="u").groupby("adm_cd2")[["sl", "length"]].sum()
    agg = agg.merge((steep_d["sl"] / steep_d["length"]).rename("steep_share").reset_index(), on="adm_cd2",
                    how="left")
    d = dong.drop(columns="geometry").merge(agg, on="adm_cd2", how="left")
    for T in THRESHOLDS_MIN:
        for m in ("hidden", "visible", "bfgap"):
            d[f"{m}_pop65_{T}"] = d["pop_65p"] * d[f"{m}_share_{T}"]
    for tag in BF_SENS:
        d[f"bfgap_{tag}_pop65_10"] = d["pop_65p"] * d[f"bfgap_{tag}_share_10"]
    d.to_csv(OUT / "dong_summary.csv", index=False, encoding="utf-8-sig")

    sums = {f"{m}_pop65_{T}": (f"{m}_pop65_{T}", "sum") for T in THRESHOLDS_MIN for m in ("hidden", "visible", "bfgap")}
    sums.update({f"bfgap_{tag}_pop65_10": (f"bfgap_{tag}_pop65_10", "sum") for tag in BF_SENS})
    gu = d.groupby("sggnm").agg(pop_total=("pop_total", "sum"), pop_65p=("pop_65p", "sum"),
                                rt_extra_min=("rt_extra_min", "mean"), **sums).reset_index()
    gu = gu.merge(steep_share.reset_index(), on="sggnm")
    gu["hidden_rate_10"] = gu["hidden_pop65_10"] / gu["pop_65p"]
    gu["bfgap_rate_10"] = gu["bfgap_pop65_10"] / gu["pop_65p"]
    gu = gu.sort_values("steep_share", ascending=False)
    gu.to_csv(OUT / "gu_summary.csv", index=False, encoding="utf-8-sig")
    stops.drop(columns="geometry").to_csv(OUT / "stops_snapped.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
