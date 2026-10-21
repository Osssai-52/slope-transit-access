"""정책 수단별 효과 비교: 점(정류장 신설) · 선(급경사 링크 개선) · 면(DRT 구역 지정)."""
import sys
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRO, FIG = ROOT / "data" / "processed", ROOT / "figures"
sys.path.insert(0, str(ROOT / "scripts"))
from config import FOCUS_GU, MAIN_T  # noqa: E402

mpl.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "savefig.bbox": "tight",
                     "savefig.dpi": 200, "axes.titleweight": "bold"})
DRT_DONGS = 3


def main():
    stop = pd.read_csv(PRO / "siting_result.csv").groupby("gu").last()
    link = pd.read_csv(PRO / "link_fix_result.csv").groupby("gu").last()
    dong = pd.read_csv(PRO / "dong_summary.csv", dtype={"adm_cd2": str})
    rows = []
    for g in FOCUS_GU:
        d = dong[dong["sggnm"] == g].nlargest(DRT_DONGS, f"bfgap_pop65_{MAIN_T}")
        rows.append({"gu": g, "base": stop.loc[g, "base_uncovered_pop65"],
                     "stop5": stop.loc[g, "cum_pop65"], "link5": link.loc[g, "cum_pop65"],
                     "drt": d[f"bfgap_pop65_{MAIN_T}"].sum() + d[f"visible_pop65_{MAIN_T}"].sum(),
                     "drt_dongs": "·".join(d["dong"])})
    t = pd.DataFrame(rows)
    t.to_csv(PRO / "intervention_compare.csv", index=False, encoding="utf-8-sig")
    print(t.round(0).to_string(index=False))

    fig, ax = plt.subplots(figsize=(9, 4.3))
    y = np.arange(len(t))
    h = 0.24
    specs = [("link5", "선: 급경사·계단 5곳 개선", "#9ecae1"), ("stop5", "점: 정차지점 5곳 신설", "#4292c6"),
             ("drt", f"면: 상위 {DRT_DONGS}개 동 DRT 구역 지정", "#c8102e")]
    for i, (c, lab, col) in enumerate(specs):
        ax.barh(y + (1 - i) * h, t[c], height=h, color=col, label=lab)
        for yy, v, b in zip(y + (1 - i) * h, t[c], t["base"]):
            ax.text(v, yy, f" {v:,.0f}명 ({v / b:.0%})", va="center", fontsize=7.5)
    ax.set_yticks(y, [f"{g}\n(대상 {b:,.0f}명)" for g, b in zip(t["gu"], t["base"])])
    ax.invert_yaxis()
    ax.margins(x=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlabel("무장애 10분권에 새로 편입되는 65세 이상 인구(추정)")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title("점·선 개선으로는 한계 → 구역(면) 단위 수요응답형 교통이 필요", loc="left", fontsize=12)
    fig.savefig(FIG / "fig8_intervention_compare.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
