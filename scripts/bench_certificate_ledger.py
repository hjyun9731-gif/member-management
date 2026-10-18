"""자격증명발급대장 응답속도 측정 (읽기 전용 GET만 호출).

사용:  BASE_URL=https://운영주소 TOKEN=<로그인 토큰> python scripts/bench_certificate_ledger.py [반복횟수]
배포 전/후에 각각 실행해 중앙값(ms)을 비교한다. 데이터는 변경하지 않는다.
"""
import os, statistics, sys, time, urllib.request

BASE = os.environ["BASE_URL"].rstrip("/")
TOKEN = os.environ["TOKEN"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
CASES = [
    ("목록 1페이지", "/api/certificate-ledger?page=1&limit=20"),
    ("목록 2페이지", "/api/certificate-ledger?page=2&limit=20"),
    ("검색(번호)", "/api/certificate-ledger?search=26-4&limit=20"),
    ("상태필터", "/api/certificate-ledger?status=%EC%9D%B8%EA%B0%80%EB%8C%80%EA%B8%B0&limit=20"),
    ("통계", "/api/certificate-ledger/stats"),
    ("예정자 목록", "/api/certificate-ledger/candidates"),
    ("번호 중복확인", "/api/certificate-ledger/number-check?number=26-425"),
]


def once(path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {TOKEN}"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    return (time.perf_counter() - t) * 1000


for label, path in CASES:
    try:
        ms = [once(path) for _ in range(N)]
        print(f"{label:<12} 첫회 {ms[0]:7.0f}ms  중앙값 {statistics.median(ms):7.0f}ms  최대 {max(ms):7.0f}ms")
    except Exception as e:
        print(f"{label:<12} 실패: {e}")
