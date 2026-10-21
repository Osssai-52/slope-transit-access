"""행정동 경계·연령별 인구·대중교통 정류장(버스/지하철 출입구)을 분석용 형태로 정리한다."""
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INT = ROOT / "data" / "interim"
INT.mkdir(parents=True, exist_ok=True)


def population():
    df = pd.read_csv(RAW / "population" / "mois_dong_age_pop_20260930.csv", encoding="cp949")
    df = df[(df["시도명"] == "서울특별시") & df["읍면동명"].notna()].copy()
    age_cols = [c for c in df.columns if c.endswith("남자") or c.endswith("여자")]
    age_cols = [c for c in age_cols if c not in ("남자", "여자")]

    def age_of(col):
        return int("".join(ch for ch in col.split("세")[0] if ch.isdigit()))

    def pop_ge(a):
        return df[[c for c in age_cols if age_of(c) >= a]].sum(axis=1)

    out = pd.DataFrame({
        "adm_cd2": df["행정기관코드"].astype(str),
        "gu": df["시군구명"], "dong": df["읍면동명"],
        "pop_total": df["계"], "pop_65p": pop_ge(65), "pop_75p": pop_ge(75),
    })
    out["share_65p"] = out["pop_65p"] / out["pop_total"]
    return out


def boundaries(pop):
    g = gpd.read_file(RAW / "boundary" / "HangJeongDong_ver20261001.geojson")
    g = g[g["sidonm"] == "서울특별시"][["adm_cd2", "sggnm", "adm_nm", "geometry"]]
    g = g.merge(pop, on="adm_cd2", how="left")
    assert g["pop_total"].notna().all()
    return g


def stops():
    bus = pd.read_excel(RAW / "busstop" / "seoul_busstop_20260902.xlsx")
    bus = bus[bus["정류소타입"] != "한강선착장"]
    bus = gpd.GeoDataFrame({"stop_id": "bus_" + bus["NODE_ID"].astype(str), "kind": "bus",
                            "subkind": bus["정류소타입"], "name": bus["정류소명"]},
                           geometry=gpd.points_from_xy(bus["X좌표"], bus["Y좌표"]), crs=4326)
    ent = gpd.read_file(INT / "subway_entrances_osm.geojson")
    ent = gpd.GeoDataFrame({"stop_id": "sub_" + ent["osmid"].astype(str), "kind": "subway",
                            "subkind": "entrance", "name": ent["name"]}, geometry=ent.geometry, crs=4326)
    # 교통약자용 지상 엘리베이터 (무장애 시나리오의 지하철 목적지)
    ev = pd.read_csv(RAW / "subway" / "seoul_subway_elevator.csv", encoding="cp949")
    ev = gpd.GeoDataFrame({"stop_id": "elev_" + ev["노드 ID"].astype(str), "kind": "subway_elev",
                           "subkind": "elevator", "name": ev["지하철역명"]},
                          geometry=gpd.GeoSeries.from_wkt(ev["노드 WKT"]), crs=4326)
    return pd.concat([bus, ent, ev], ignore_index=True)


def main():
    pop = population()
    dong = boundaries(pop)
    dong.to_file(INT / "seoul_dong.gpkg", driver="GPKG")
    s = stops()
    s.to_file(INT / "transit_stops.gpkg", driver="GPKG")
    print(f"동 {len(dong)}개, 65세+ {int(dong.pop_65p.sum()):,}명 / 총 {int(dong.pop_total.sum()):,}명")
    print(s.groupby(["kind", "subkind"]).size())


if __name__ == "__main__":
    main()
