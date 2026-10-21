"""FABDEM v1-2 (Hawker et al., 2022) 10x10도 zip에서 서울 타일(N37E126, N37E127)만 추출한다.

전체 zip(640MB)을 받지 않고, zip 중앙 디렉터리를 읽어 필요한 멤버의 바이트 구간만
HTTP Range 요청(병렬)으로 받은 뒤 로컬에서 압축을 푼다.
라이선스: CC BY-NC-SA 4.0 (비상업적 이용, 출처표시)
"""
import struct
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

URL = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/N30E120-N40E130_FABDEM_V1-2.zip"
TILES = ["N37E126", "N37E127"]
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "fabdem"
CHUNK = 512 * 1024
WORKERS = 32


def get_range(start, end):
    req = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
    for _ in range(5):
        try:
            return urllib.request.urlopen(req, timeout=120).read()
        except Exception:
            continue
    raise RuntimeError(f"range {start}-{end} failed")


def central_directory():
    size = int(urllib.request.urlopen(urllib.request.Request(URL, method="HEAD")).headers["Content-Length"])
    tail = get_range(size - 65536, size - 1)
    i = tail.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    cd = get_range(cd_off, cd_off + cd_size - 1)
    entries, p = [], 0
    while cd[p:p + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        off, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode()
        entries.append((name, method, csize, usize, off))
        p += 46 + nlen + elen + clen
    return entries


def fetch_member(name, method, csize, usize, off):
    hdr = get_range(off, off + 29)
    nlen, elen = struct.unpack("<HH", hdr[26:30])
    start = off + 30 + nlen + elen
    ranges = [(s, min(s + CHUNK, start + csize) - 1) for s in range(start, start + csize, CHUNK)]
    with ThreadPoolExecutor(WORKERS) as ex:
        data = b"".join(ex.map(lambda r: get_range(*r), ranges))
    raw = zlib.decompress(data, -15) if method == zipfile.ZIP_DEFLATED else data
    assert len(raw) == usize, (name, len(raw), usize)
    dest = OUT / Path(name).name
    dest.write_bytes(raw)
    print(f"{name}: {csize / 1e6:.1f}MB -> {dest}", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for e in central_directory():
        if any(t in e[0] for t in TILES) and e[0].endswith(".tif"):
            fetch_member(*e)


if __name__ == "__main__":
    main()
