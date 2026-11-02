"""테스트 모듈별 별도 프로세스/임시 SQLite로 전체 회귀 테스트를 재현한다.

기존 테스트는 모듈 전역 엔진·스키마 준비 캐시를 공유하므로 단일 프로세스에서
앞선 테스트의 임시 DB/백그라운드 상태가 다음 테스트에 유입될 수 있다.
운영 DATABASE_URL을 사용하지 않으며 각 모듈마다 새 DB를 지정한다.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    passed = skipped = 0
    failures = []
    with tempfile.TemporaryDirectory(prefix="member-management-tests-") as temp:
        for i, path in enumerate(sorted((root / "tests").glob("test_*.py"))):
            env = dict(os.environ, DATABASE_URL=f"sqlite:///{temp}/module-{i}.db", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(path), "--tb=short", "-p", "no:cacheprovider"],
                                    cwd=root, env=env, text=True, capture_output=True)
            output = result.stdout + result.stderr
            lines = [s for s in output.splitlines() if re.search(r"\d+ (passed|skipped|failed|error)", s)]
            summary = lines[-1] if lines else "No test summary"
            print(path.name + ": " + summary, flush=True)
            p, s = re.search(r"(\d+) passed", summary), re.search(r"(\d+) skipped", summary)
            passed += int(p[1]) if p else 0; skipped += int(s[1]) if s else 0
            if result.returncode:
                failures.append(path.name); print(output, flush=True)
    print(f"TOTAL: {passed} passed, {skipped} skipped; failing modules: {len(failures)}", flush=True)
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
