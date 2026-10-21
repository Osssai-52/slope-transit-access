"""정책 시뮬레이션: 신규 정차지점(마을버스 노선 조정·DRT 정차점 등)을 K곳 추가할 때
'무장애 경로(계단 없음, 경사 1/12 이하)로 10분 안에 정류장 도달'이 새로 가능해지는 65세 이상 인구를
최대화하는 위치를 탐욕 알고리즘(최대 커버리지 문제)으로 찾는다.

- 대상: 집중분석 구의 출발지 중 시나리오 E(무장애) 도보시간이 T분을 넘는 지점
- 후보: 차량 통행이 가능한 도로(residential 이상)에 접한 노드
- 커버 판정: 후보까지 시나리오 E 도보시간 ≤ T분
- 가중치: 행정동 65세 이상 인구를 동 내부 출발지 노드에 균등 배분
"""
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

ROOT = Path(__file__).resolve().parents[1]
INT, PRO, FIG = ROOT / "data" / "interim", ROOT / "data" / "processed", ROOT / "figures"
sys.path.insert(0, str(ROOT / "scripts"))
from config import FOCUS_GU, MAIN_T  # noqa: E402

mpl.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "savefig.bbox": "tight",
                     "savefig.dpi": 200})
CRS = 5179
K = 5
VEHICLE = {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street",
           "primary_link", "secondary_link", "tertiary_link"}
BATCH = 150


def fix_links(gu, gd, sub_nodes, e_all, R, idx, target, tgt_idx, tgt_w, T, max_len=200.0):
    """정책 시뮬레이션 2: 급경사·계단 링크 K곳을 무장애로 개선(경사형 엘리베이터, 경사로·리프트 등)할 때
    새로 무장애 10분권에 들어오는 고령자를 최대화하는 링크를 탐욕적으로 고른다.

    링크 u→v를 개선하면, 출발지 o는 (o→u 무장애 시간) + (u→v 시간) + (v→정류장 무장애 시간) ≤ T일 때 커버된다."""
    tE = sub_nodes["t_E_barrier_free"]
    cand = e_all[e_all["barrier"] & ~e_all["highway"].isin(["path", "track", "bridleway"])
                 & (e_all["length"] <= max_len)].copy()
    cand = cand[cand["u"].isin(sub_nodes.index[sub_nodes["sggnm"] == gu])]
    cand["budget"] = T - cand["t_D_elder_slope"].to_numpy() - tE.reindex(cand["v"]).to_numpy()
    cand = cand[cand["budget"] > 0].reset_index(drop=True)
    u_idx = idx.loc[cand["u"]].to_numpy()
    cover = []
    for i in range(0, len(cand), BATCH):
        D = dijkstra(R, directed=True, indices=u_idx[i:i + BATCH], limit=T)
        for row, b in zip(D, cand["budget"].to_numpy()[i:i + BATCH]):
            cover.append(np.flatnonzero(row[tgt_idx] <= b))
    print(f"{gu}: link candidates {len(cand):,}", flush=True)

    covered = np.zeros(len(tgt_idx), bool)
    out, picked = [], []
    for k in range(1, K + 1):
        gains = np.array([tgt_w[c[~covered[c]]].sum() for c in cover])
        j = int(gains.argmax())
        covered[cover[j]] = True
        r = cand.iloc[j]
        mid = r.geometry.interpolate(0.5, normalized=True)
        dong_name = sub_nodes.loc[r["u"], "dong"]
        out.append({"gu": gu, "rank": k, "u": r["u"], "v": r["v"], "dong": dong_name, "highway": r["highway"],
                    "length": r["length"], "abs_grade": r["abs_grade"], "gain_pop65": gains[j],
                    "cum_pop65": tgt_w[covered].sum(), "base_uncovered_pop65": tgt_w.sum(),
                    "x": mid.x, "y": mid.y})
        picked.append(r.geometry)

    fig, ax = plt.subplots(figsize=(8, 7))
    bb = gd.total_bounds
    e_all.cx[bb[0]:bb[2], bb[1]:bb[3]].plot(ax=ax, color="#e6e6e6", linewidth=0.3)
    gd.boundary.plot(ax=ax, color="#8a8a8a", linewidth=0.4)
    unc, newc = target[~covered], target[covered]
    ax.scatter(unc.geometry.x, unc.geometry.y, s=1.5, c="#bdbdbd", linewidths=0, label="여전히 무장애 10분권 밖")
    ax.scatter(newc.geometry.x, newc.geometry.y, s=3, c="#2e6fbb", linewidths=0, label="링크 개선으로 해소")
    gpd.GeoSeries(picked, crs=CRS).plot(ax=ax, color="#c8102e", linewidth=4, zorder=5)
    for r in out:
        ax.annotate(f"{r['rank']}. {r['dong']} {'계단' if r['highway'] == 'steps' else '급경사로'} "
                    f"{r['length']:.0f}m (+{r['gain_pop65']:,.0f}명)", (r["x"], r["y"]), xytext=(6, 6),
                    textcoords="offset points", fontsize=8, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.plot([], [], color="#c8102e", lw=3, label="개선 대상 링크(경사형 엘리베이터·경사로 등)")
    ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3]); ax.set_axis_off()
    ax.legend(loc="lower left", fontsize=8, markerscale=1.5)
    tot = out[-1]["cum_pop65"]
    ax.set_title(f"{gu}: 급경사·계단 {K}곳 개선 시 고령자 약 {tot:,.0f}명이 무장애 10분권에 편입\n"
                 f"(현재 무장애 10분권 밖 고령자 {tgt_w.sum():,.0f}명 중 {tot / tgt_w.sum():.0%})", loc="left",
                 fontsize=11)
    fig.savefig(FIG / f"fig7_{gu}_link_fix.png")
    plt.close(fig)
    return out


def main():
    T = MAIN_T * 60
    nodes = gpd.read_parquet(PRO / "node_access.parquet")
    edges = gpd.read_parquet(PRO / "edges_grade.parquet")
    dong = gpd.read_file(INT / "seoul_dong.gpkg").to_crs(CRS)
    summ = pd.read_csv(PRO / "dong_summary.csv", dtype={"adm_cd2": str})
    rows, picks_all, link_rows = [], [], []

    for gu in FOCUS_GU:
        gd = dong[dong["sggnm"] == gu]
        poly = gd.union_all().buffer(800)
        sub_nodes = nodes[nodes.geometry.within(poly)]
        keep = set(sub_nodes.index)
        e = edges[edges["u"].isin(keep) & edges["v"].isin(keep)]
        idx = pd.Series(np.arange(len(sub_nodes)), index=sub_nodes.index)
        # 역방향 그래프: 후보 c에서 출발하는 다익스트라 = 각 출발지 → c 도보시간
        e = e[~e["barrier"]]
        w = e.groupby(["u", "v"])["t_D_elder_slope"].min().reset_index()
        n = len(sub_nodes)
        R = csr_matrix((w["t_D_elder_slope"].to_numpy(), (idx.loc[w["v"]].to_numpy(), idx.loc[w["u"]].to_numpy())),
                       shape=(n, n))

        o = sub_nodes[(sub_nodes["sggnm"] == gu) & sub_nodes["origin"]].copy()
        per_node = summ.set_index("adm_cd2")["pop_65p"] / summ.set_index("adm_cd2")["n_origin"]
        o["w65"] = o["adm_cd2"].map(per_node).fillna(0)
        target = o[o["t_E_barrier_free"] > T]
        tgt_idx = idx.loc[target.index].to_numpy()
        tgt_w = target["w65"].to_numpy()

        veh_nodes = set(edges.loc[edges["highway"].isin(VEHICLE), "u"])
        cand = sub_nodes[sub_nodes.index.isin(veh_nodes) & (sub_nodes["sggnm"] == gu)
                         & (sub_nodes["t_E_barrier_free"] > T * 0.5)]
        cand_idx = idx.loc[cand.index].to_numpy()
        print(f"{gu}: targets {len(target):,} (65+ {tgt_w.sum():,.0f}), candidates {len(cand):,}", flush=True)

        cover = []
        for i in range(0, len(cand_idx), BATCH):
            D = dijkstra(R, directed=True, indices=cand_idx[i:i + BATCH], limit=T)
            cover.extend([np.flatnonzero(np.isfinite(row[tgt_idx])) for row in D])

        covered = np.zeros(len(tgt_idx), bool)
        for k in range(1, K + 1):
            gains = np.array([tgt_w[c[~covered[c]]].sum() for c in cover])
            j = int(gains.argmax())
            covered[cover[j]] = True
            node = cand.index[j]
            r = cand.loc[node]
            rows.append({"gu": gu, "rank": k, "node": node, "dong": r["dong"], "gain_pop65": gains[j],
                         "cum_pop65": tgt_w[covered].sum(), "base_uncovered_pop65": tgt_w.sum(),
                         "x": r.geometry.x, "y": r.geometry.y, "elev": r["elev"]})
            picks_all.append((gu, node, cover[j], target.index))

        fig, ax = plt.subplots(figsize=(8, 7))
        bb = gd.total_bounds
        edges.cx[bb[0]:bb[2], bb[1]:bb[3]].plot(ax=ax, color="#e6e6e6", linewidth=0.3)
        gd.boundary.plot(ax=ax, color="#8a8a8a", linewidth=0.4)
        unc = target[~covered]
        newc = target[covered]
        ax.scatter(unc.geometry.x, unc.geometry.y, s=1.5, c="#bdbdbd", linewidths=0, label="여전히 무장애 10분권 밖")
        ax.scatter(newc.geometry.x, newc.geometry.y, s=3, c="#2e6fbb", linewidths=0, label="신규 정차지점으로 해소")
        pk = pd.DataFrame([r for r in rows if r["gu"] == gu])
        ax.scatter(pk["x"], pk["y"], marker="*", s=220, c="#d1495b", edgecolors="white", linewidths=0.8,
                   zorder=5, label="신규 정차지점 후보")
        for _, r in pk.iterrows():
            ax.annotate(f"{int(r['rank'])}. {r['dong']} (+{r['gain_pop65']:,.0f}명)", (r["x"], r["y"]),
                        xytext=(6, 6), textcoords="offset points", fontsize=8, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
        ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3]); ax.set_axis_off()
        ax.legend(loc="lower left", fontsize=8, markerscale=1.5)
        tot = pk["cum_pop65"].iloc[-1]
        ax.set_title(f"{gu}: 정차지점 {K}곳 추가 시 고령자 약 {tot:,.0f}명이 무장애 10분권에 편입\n"
                     f"(현재 무장애 10분권 밖 고령자 {tgt_w.sum():,.0f}명 중 {tot / tgt_w.sum():.0%})", loc="left",
                     fontsize=11)
        fig.savefig(FIG / f"fig6_{gu}_siting.png")
        plt.close(fig)

        link_rows.extend(fix_links(gu, gd, sub_nodes, e_all=edges[edges["u"].isin(keep) & edges["v"].isin(keep)],
                                   R=R, idx=idx, target=target, tgt_idx=tgt_idx, tgt_w=tgt_w, T=T))

    pd.DataFrame(link_rows).to_csv(PRO / "link_fix_result.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(link_rows)[["gu", "rank", "dong", "highway", "length", "abs_grade", "gain_pop65",
                                    "cum_pop65"]].round(2).to_string(index=False))
    out = pd.DataFrame(rows)
    out.to_csv(PRO / "siting_result.csv", index=False, encoding="utf-8-sig")
    print(out[["gu", "rank", "dong", "gain_pop65", "cum_pop65", "base_uncovered_pop65", "elev"]].round(0)
          .to_string(index=False))


if __name__ == "__main__":
    main()
