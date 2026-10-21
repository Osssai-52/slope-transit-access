# 평면거리로는 안 보이는 교통 소외
### 경사를 반영한 대중교통 보행 접근성 격차 분석 (서울)

> AI와 함께하는 교통문제 해결을 위한 데이터 분석 공모전 (숲과나눔 풀씨행동연구소 · 한겨레) 출품 코드

대중교통 접근성은 보통 "정류장까지 몇 m"로 측정합니다. 하지만 급경사 동네에서는 같은 300m도 고령자에게 훨씬 먼 길이고, 보행보조기·휠체어 이용자에게는 갈 수 없는 길입니다.
이 저장소는 **실제 보행 네트워크 + 건물·수목이 제거된 지형고도(FABDEM)** 로 모든 보행 링크의 경사를 계산하고,
**"평지라면 정류장 10분 안이지만, 계단과 법정 기준(1/12)을 넘는 경사를 피하면 정류장에 갈 수 없는 고령자"**(무장애 단절)를 행정동 단위로 추정합니다.

- 분석보고서 초안: [docs/report_draft.md](docs/report_draft.md)
- AI 활용 내역: [docs/AI_USAGE.md](docs/AI_USAGE.md)
- 결과표: `data/processed/*.csv`, 그림: `figures/`

## 실행 방법

Python 3.11 기준입니다. 원자료(약 400MB)는 용량·라이선스 문제로 저장소에 포함하지 않았습니다. 아래 경로에 받아 둔 뒤 실행하세요.

| 파일 경로 | 받는 곳 |
|---|---|
| `data/raw/busstop/seoul_busstop_20260902.xlsx` | 서울 열린데이터광장 OA-15067 → 파일 '서울시버스정류소위치정보(20260902).xlsx' |
| `data/raw/subway/seoul_subway_elevator.csv` | 서울 열린데이터광장 OA-21212 → 내려받기(CSV) |
| `data/raw/population/mois_dong_age_pop_20260930.csv` | 공공데이터포털 15097972 → CSV 다운로드 |
| `data/raw/boundary/HangJeongDong_ver20261001.geojson` | https://github.com/vuski/admdongkor/tree/master/ver20261001 |
| `data/raw/osm/south-korea-261019.osm.pbf` | https://download.geofabrik.de/asia/south-korea-261019.osm.pbf |
| `data/raw/fabdem/N37E126_FABDEM_V1-2.tif`, `N37E127_...tif` | `scripts/00_fetch_fabdem_tiles.py`가 자동으로 받음 |

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
python scripts/00_fetch_fabdem_tiles.py           # FABDEM 서울 타일 2개 (~45MB)
python scripts/01_build_osm_network.py            # OSM → 서울 보행 네트워크 (최초 1회 약 10분)
python scripts/02_prepare_inputs.py               # 인구·정류장 정리
python scripts/03_accessibility.py                # 경사 반영 접근성 계산 (서울 전역)
python scripts/04_figures.py                      # 지도·그래프
python scripts/05_siting.py                       # 정차지점 신설·급경사 링크 개선 시뮬레이션
python scripts/06_intervention_chart.py           # 정책 수단별 효과 비교
```

Windows에서 한글 출력이 깨지면 `python -X utf8 scripts/...`로 실행하세요. 지도 글꼴은 맑은 고딕(Malgun Gothic)을 사용합니다.

## 주요 결과

| | 서울 전체 | 관악구 | 서대문구 | 종로구 |
|---|---|---|---|---|
| 주거지 보행로 중 경사 1/12 초과 | — | 20.8% | 21.2% | 20.1% |
| 정류장 10분 초과 지점 (평지 가정) | 6.7% | 3.5% | 10.5% | 6.5% |
| 정류장 10분 초과 지점 (무장애 경로) | 16.8% | 29.0% | 35.8% | 24.5% |
| 무장애 단절 65세 이상 인구 | 189,303명 (9.6%) | 23,418명 (25%) | 16,450명 (26%) | 8,438명 (27%) |

- 보통 걸음의 고령자에게 경사가 더하는 왕복 시간은 평균 1분 미만입니다. 반면 계단·급경사를 피해야 하는 사람에게는 정류장으로 가는 길 자체가 없는 지점이 20~29%입니다.
- 정차지점 5곳 신설은 단절 인구의 4~8%, 급경사 링크 5곳 개선은 2~4%만 해소합니다. 상위 3개 동을 수요응답형 교통(DRT) 구역으로 지정하면 28~46%를 해소합니다.

## 방법 요약

| 단계 | 내용 |
|---|---|
| 보행 네트워크 | OSM 보행 가능 도로(osmnx `walk` 기준과 동일한 필터), 서울 + 1km 버퍼 |
| 링크 경사 | FABDEM 30m를 EPSG:5179로 재투영, 링크를 15m 간격으로 표고 샘플링(쌍선형 보간), 구간별 경사 ±40% 제한, 교량·터널은 경사 0 |
| 보행속도 | Tobler(1993) 함수를 평지=1로 정규화한 배율 × 기준속도. 성인 1.33m/s, 고령자 0.7m/s(경찰청 교통신호기 매뉴얼 2020 개정 교통약자 기준), 고령자 계단 구간 속도 ×0.6 |
| 목적지 | 서울시 버스정류소(마을버스 포함, 한강선착장 제외) + OSM 지하철 출입구 |
| 도보시간 | 모든 정류장을 출발점으로 하는 역방향 다중출발 다익스트라 (오르막/내리막 방향 구분) |
| 출발지 | 행정동 내부 네트워크 노드 중 공원·산림 등 녹지와 등산로 전용 노드 제외 |
| 무장애 단절 | 고령자·평지(C)로는 10분 이내지만 계단·경사 1/12 초과 구간을 피하는 경로(E, 지하철은 엘리베이터로만 진입)로는 10분 초과 |
| 경사 시간지연 | C 왕복 20분 이내지만 고령자·경사(D) 왕복 20분 초과 |
| 구 선정 | 주거지 보행로 중 1/12 초과 구간 비율 상위 3개 구 |
| 인구 배분 | 행정동 65세 이상 인구를 동 내부 출발지 노드에 균등 배분 |

## 데이터

| 데이터 | 제공기관 | 플랫폼 | URL | 라이선스 |
|---|---|---|---|---|
| 서울시 버스정류소 위치정보 (2026.09.02) | 서울특별시 | 서울 열린데이터광장 | https://data.seoul.go.kr/dataList/OA-15067/S/1/datasetView.do | 공공누리 |
| 행정안전부 지역별(행정동) 성별 연령별 주민등록 인구수 (2026.09.30) | 행정안전부 | 공공데이터포털 | https://www.data.go.kr/data/15097972/fileData.do | 공공누리 |
| 행정동 경계 (ver20261001) | 통계청 SGIS (가공: vuski/admdongkor) | GitHub | https://github.com/vuski/admdongkor | KOGL 1 / CC BY 4.0 |
| OpenStreetMap 한국 추출본 (2026.10.19) | OpenStreetMap contributors | Geofabrik | https://download.geofabrik.de/asia/south-korea.html | ODbL 1.0 |
| FABDEM v1-2 | University of Bristol (Hawker et al., 2022) | data.bris | https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn | CC BY-NC-SA 4.0 |
| 서울시 지하철역 엘리베이터 위치정보 | 서울특별시 | 서울 열린데이터광장 | https://data.seoul.go.kr/dataList/OA-21212/S/1/datasetView.do | 공공누리 |

## 한계

- 30m DEM은 짧은 급경사 골목·계단을 평탄하게 만들어 **경사 효과를 과소추정**합니다 (보수적 추정).
- 동 인구를 노드에 균등 배분하므로 동 내부 인구 분포는 반영하지 못합니다.
- 배차간격·운행시간 등 정류장의 서비스 수준은 고려하지 않았습니다.
- OSM 보도·계단 정보의 완전성은 지역마다 다릅니다.

## 참고문헌

- Tobler, W. (1993). *Three presentations on geographical analysis and modeling.* NCGIA Technical Report 93-1.
- Hawker, L. et al. (2022). A 30 m global map of elevation with forests and buildings removed. *Environmental Research Letters*, 17, 024016.
- Boeing, G. (2025). Modeling and Analyzing Urban Networks and Amenities with OSMnx. *Geographical Analysis*.
