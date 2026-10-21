"""서울 전역 스크리닝 + 집중분석 구 지도/그래프 생성."""
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap

ROOT = Path(__file__).resolve().parents[1]
INT, PRO, FIG = ROOT / "data" / "interim", ROOT / "data" / "processed", ROOT / "figures"
FIG.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts"))
from config import FOCUS_GU, MAIN_T  # noqa: E402

mpl.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "figure.dpi": 150,
                     "savefig.bbox": "tight", "savefig.dpi": 200, "axes.titleweight": "bold"})
CRS = 5179
INK, MUTED, GRID = "#222222", "#8a8a8a", "#e9e9e9"
ACCENT, BLUE, BROWN = "#c8102e", "#2e6fbb", "#a0522d"
CREDIT = ("자료: 행정안전부 주민등록인구(2026.9), 서울시 버스정류소 위치정보(2026.9)·지하철역 엘리베이터 위치정보, "
          "© OpenStreetMap contributors, FABDEM v1-2(Hawker et al., 2022), 통계청 SGIS 행정동 경계(가공: vuski/admdongkor)")
TIME_BINS = [0, 3, 6, 10, 15, 20, np.inf]
TIME_CMAP = ListedColormap(["#1a9850", "#91cf60", "#d9ef8b", "#fdae61", "#f46d43", "#a50026"])
TIME_LABELS = ["3분 이하", "3–6분", "6–10분", "10–15분", "15–20분", "20분 초과·경로 없음"]


def credit(fig, y=-0.01):
    fig.text(0.01, y, CREDIT, fontsize=6, color=MUTED, ha="left", va="top")


def load():
    dong = gpd.read_file(INT / "seoul_dong.gpkg").to_crs(CRS)
    summ = pd.read_csv(PRO / "dong_summary.csv", dtype={"adm_cd2": str})
    dong = dong[["adm_cd2", "geometry"]].merge(summ, on="adm_cd2")
    gu = pd.read_csv(PRO / "gu_summary.csv")
    nodes = gpd.read_parquet(PRO / "node_access.parquet")
    edges = gpd.read_parquet(PRO / "edges_grade.parquet")
    stops = gpd.read_file(INT / "transit_stops.gpkg").to_crs(CRS)
    return dong, gu, nodes, edges, stops


def fig_screening(dong, gu):
    T = MAIN_T
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 6.4), gridspec_kw={"width_ratios": [1.2, 1]})
    col = f"bfgap_share_{T}"
    bins = [0, 0.05, 0.15, 0.30, 0.45, 1.01]
    cmap = ListedColormap(["#f7f4f2", "#f6c6b8", "#ee8f7a", "#c8102e", "#7a0019"])
    dong.plot(ax=ax1, column=col, cmap=cmap, norm=BoundaryNorm(bins, cmap.N), edgecolor="white", linewidth=0.25)
    gub = dong.dissolve("sggnm")
    gub.boundary.plot(ax=ax1, color=INK, linewidth=0.5)
    for g in FOCUS_GU:
        gub.loc[[g]].boundary.plot(ax=ax1, color=INK, linewidth=2.2)
        c = gub.loc[g].geometry.representative_point()
        ax1.annotate(g, (c.x, c.y), ha="center", fontsize=10, fontweight="bold", color=INK,
                     bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))
    labels = ["5% 미만", "5–15%", "15–30%", "30–45%", "45% 이상"]
    ax1.legend(handles=[mpl.patches.Patch(color=cmap(i), label=l) for i, l in enumerate(labels)],
               title="고령자 중 해당 비율", loc="lower left", fontsize=8, title_fontsize=8, frameon=False)
    ax1.set_axis_off()
    ax1.set_title(f"계단 없이 경사 1/12 이하인 길로는 {T}분 안에\n정류장에 갈 수 없는 고령자 비율 (행정동)",
                  loc="left", fontsize=12)

    g = gu.sort_values("steep_share")
    colors = [ACCENT if n in FOCUS_GU else "#c9c9c9" for n in g["sggnm"]]
    ax2.barh(g["sggnm"], g["steep_share"] * 100, color=colors, height=0.7)
    for y, (v, r) in enumerate(zip(g["steep_share"], g[f"bfgap_rate_{T}"])):
        ax2.text(v * 100, y, f" {v:.0%}  (무장애 단절 고령자 {r:.0%})", va="center", fontsize=7, color=INK)
    ax2.set_xlabel("주거지 보행로 중 경사 1/12 초과 구간 비율(%)", fontsize=9)
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.tick_params(labelsize=8)
    ax2.margins(x=0.55)
    ax2.set_title("자치구 경사 순위 → 상위 3개 구 집중분석", loc="left", fontsize=12)
    credit(fig)
    fig.savefig(FIG / "fig1_seoul_screening.png")
    plt.close(fig)


def gu_frame(gu_name, dong, nodes, edges, stops):
    gd = dong[dong["sggnm"] == gu_name]
    bb = gd.total_bounds
    pad = 200
    e = edges.cx[bb[0] - pad:bb[2] + pad, bb[1] - pad:bb[3] + pad]
    n = nodes[(nodes["sggnm"] == gu_name) & nodes["origin"]]
    s = stops.cx[bb[0] - pad:bb[2] + pad, bb[1] - pad:bb[3] + pad]
    return gd, bb, e, n, s


def fig_compare(gu_name, dong, nodes, edges, stops):
    T = MAIN_T
    gd, bb, e, n, s = gu_frame(gu_name, dong, nodes, edges, stops)
    norm = BoundaryNorm(TIME_BINS[:-1] + [1e9], TIME_CMAP.N)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.6))
    panels = [(axes[0], "t_C_elder_flat", s[s["kind"].isin(["bus", "subway"])],
               "① 평지라고 가정할 때 (기존 거리 기반 관점)"),
              (axes[1], "t_E_barrier_free", s[s["kind"].isin(["bus", "subway_elev"])],
               "② 계단·급경사(1/12 초과)를 피해야 할 때")]
    for ax, col, ss, title in panels:
        e.plot(ax=ax, color=GRID, linewidth=0.3)
        v = np.where(np.isfinite(n[col]), n[col] / 60, 1e8)
        ax.scatter(n.geometry.x, n.geometry.y, c=v, cmap=TIME_CMAP, norm=norm, s=1.4, linewidths=0)
        gd.boundary.plot(ax=ax, color=MUTED, linewidth=0.4)
        gd.dissolve().boundary.plot(ax=ax, color=INK, linewidth=1.0)
        ax.scatter(ss.geometry.x, ss.geometry.y, s=2.5, c=INK, marker="s", linewidths=0)
        ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3]); ax.set_axis_off()
        share = (n[col] > T * 60).mean()
        ax.set_title(f"{title}\n정류장까지 고령자 도보 {T}분 초과 지점: {share:.0%}", loc="left", fontsize=10.5)
    handles = [mpl.lines.Line2D([], [], marker="o", ls="", color=TIME_CMAP(i), label=l)
               for i, l in enumerate(TIME_LABELS)]
    handles.append(mpl.lines.Line2D([], [], marker="s", ls="", color=INK, markersize=3,
                                    label="정류장 (②는 지하철 엘리베이터만)"))
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.06), ncol=7, frameon=False, fontsize=8)
    fig.suptitle(f"{gu_name} — 같은 동네, 다른 거리: 고령자(0.7m/s) 정류장 도보시간", x=0.01, y=1.04, ha="left",
                 fontsize=13, fontweight="bold")
    fig.subplots_adjust(wspace=0.03)
    credit(fig, y=0.0)
    fig.savefig(FIG / f"fig2_{gu_name}_compare.png")
    plt.close(fig)


def fig_barriers(gu_name, dong, nodes, edges, stops, top_k=5):
    T = MAIN_T
    gd, bb, e, n, s = gu_frame(gu_name, dong, nodes, edges, stops)
    fig, ax = plt.subplots(figsize=(8, 7.2))
    e.plot(ax=ax, color=GRID, linewidth=0.3)
    e[(e["abs_grade"] > 1 / 12) & ~e["highway"].isin(["path", "track", "bridleway"])].plot(
        ax=ax, color=BROWN, linewidth=0.7, alpha=0.75)
    e[e["highway"] == "steps"].plot(ax=ax, color=INK, linewidth=1.2)
    gd.boundary.plot(ax=ax, color=MUTED, linewidth=0.4)
    gap = n[n[f"bfgap_{T}"]]
    ax.scatter(gap.geometry.x, gap.geometry.y, s=2.2, c=ACCENT, linewidths=0, zorder=3)
    for _, r in gd.nlargest(top_k, f"bfgap_pop65_{T}").iterrows():
        c = r.geometry.representative_point()
        ax.annotate(f"{r['dong']}\n{r[f'bfgap_pop65_{T}']:,.0f}명", (c.x, c.y), ha="center", fontsize=8,
                    color=INK, fontweight="bold", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=ACCENT, lw=0.6, alpha=0.9))
    handles = [mpl.lines.Line2D([], [], color=BROWN, lw=1.5, label="경사 1/12(8.3%) 초과 보행로"),
               mpl.lines.Line2D([], [], color=INK, lw=1.5, label="계단"),
               mpl.lines.Line2D([], [], marker="o", ls="", color=ACCENT, label=f"평지면 {T}분 안이지만 무장애 경로로는 못 가는 지점")]
    ax.legend(handles=handles, loc="lower left", fontsize=7.5, frameon=True)
    ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3]); ax.set_axis_off()
    tot = gd[f"bfgap_pop65_{T}"].sum()
    rate = tot / gd["pop_65p"].sum()
    ax.set_title(f"{gu_name}: 무장애 경로 단절 고령자 약 {tot:,.0f}명 (구 고령인구의 {rate:.0%})", loc="left",
                 fontsize=12)
    credit(fig, y=0.02)
    fig.savefig(FIG / f"fig3_{gu_name}_barriers.png")
    plt.close(fig)


def fig_top_dongs(dong, k=12):
    T = MAIN_T
    d = dong[dong["sggnm"].isin(FOCUS_GU)].nlargest(k, f"bfgap_pop65_{T}").iloc[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    lab = d["sggnm"] + " " + d["dong"]
    ax.barh(lab, d[f"bfgap_pop65_{T}"], color=ACCENT, height=0.65)
    for y, (v, sh, r) in enumerate(zip(d[f"bfgap_pop65_{T}"], d[f"bfgap_share_{T}"], d["share_65p"])):
        ax.text(v, y, f"  {v:,.0f}명 · 동 고령자의 {sh:.0%} · 고령화율 {r:.0%}", va="center", fontsize=7.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlabel(f"무장애 경로로 정류장 {T}분 접근 불가 65세 이상 인구(추정)")
    ax.margins(x=0.6)
    ax.set_title("우선 개입이 필요한 행정동 (3개 구 상위 12곳)", loc="left", fontsize=12)
    credit(fig)
    fig.savefig(FIG / "fig4_top_dongs.png")
    plt.close(fig)


def fig_sensitivity(gu):
    g = gu.set_index("sggnm")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.2))
    Ts = [5, 8, 10, 15]
    for name in g.index:
        f = name in FOCUS_GU
        ax1.plot(Ts, [g.loc[name, f"bfgap_pop65_{t}"] / 1000 for t in Ts], marker="o" if f else None,
                 color=ACCENT if f else "#d0d0d0", lw=2 if f else 0.8, zorder=3 if f else 1)
        if f:
            ax1.text(Ts[-1] + 0.3, g.loc[name, f"bfgap_pop65_{Ts[-1]}"] / 1000, name, va="center", fontsize=8)
    ax1.set_xticks(Ts, [f"{t}분" for t in Ts])
    ax1.set_xlabel("도보 접근 기준 시간")
    ax1.set_ylabel("무장애 단절 고령자 (천 명)")
    ax1.set_title("① 8분 이후 기준을 늘려도 줄지 않음 → 시간이 아니라 길의 문제", loc="left", fontsize=11)

    cols = [("bfgap_pop65_10", "1/12\n(8.3%)"), ("bfgap_g10_pop65_10", "10%"), ("bfgap_g125_pop65_10", "12.5%")]
    x = np.arange(len(cols))
    for name in g.index:
        f = name in FOCUS_GU
        ax2.plot(x, [g.loc[name, c] / 1000 for c, _ in cols], marker="o" if f else None,
                 color=ACCENT if f else "#d0d0d0", lw=2 if f else 0.8, zorder=3 if f else 1)
        if f:
            ax2.text(x[-1] + 0.05, g.loc[name, cols[-1][0]] / 1000, name, va="center", fontsize=8)
    seoul = [g[c].sum() / 1000 for c, _ in cols]
    ax2.set_xticks(x, [l for _, l in cols])
    ax2.set_xlabel("통행 불가로 보는 경사 기준")
    ax2.set_title(f"② 기준을 완화해도 상위 구 유지 (서울 전체 {seoul[0]:.0f}천→{seoul[-1]:.0f}천 명)",
                  loc="left", fontsize=11)
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(FIG / "fig5_sensitivity.png")
    plt.close(fig)


def table_scenarios(nodes, dong):
    rows = []
    for gname in FOCUS_GU + ["서울 전체"]:
        n = nodes[nodes["origin"] & ((nodes["sggnm"] == gname) | (gname == "서울 전체"))]
        d = dong if gname == "서울 전체" else dong[dong["sggnm"] == gname]
        rows.append({
            "구": gname, "65세 이상": int(d["pop_65p"].sum()),
            "A 성인·평지 중앙값(분)": n["t_A_adult_flat"].median() / 60,
            "D 고령자·경사 중앙값(분)": n["t_D_elder_slope"].median() / 60,
            "경사로 늘어난 왕복시간 평균(분)": n["rt_extra_min"].mean(),
            "귀갓길 오르막 추가(분)": n["uphill_home_min"].mean(),
            "무장애 경로 없음 지점 비율": n["bf_unreachable"].mean(),
            "무장애 단절 고령자(명)": d[f"bfgap_pop65_{MAIN_T}"].sum(),
            "무장애 단절 비율": d[f"bfgap_pop65_{MAIN_T}"].sum() / d["pop_65p"].sum(),
            "경사 시간지연 소외 고령자(명)": d[f"hidden_pop65_{MAIN_T}"].sum(),
        })
    t = pd.DataFrame(rows)
    t.to_csv(PRO / "table_scenarios.csv", index=False, encoding="utf-8-sig")
    print(t.round(2).to_string(index=False))


def main():
    dong, gu, nodes, edges, stops = load()
    fig_screening(dong, gu)
    for g in FOCUS_GU:
        fig_compare(g, dong, nodes, edges, stops)
        fig_barriers(g, dong, nodes, edges, stops)
    fig_top_dongs(dong)
    fig_sensitivity(gu)
    table_scenarios(nodes, dong)
    print("figures ->", FIG)


if __name__ == "__main__":
    main()
