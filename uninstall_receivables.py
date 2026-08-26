#!/usr/bin/env python3
from pathlib import Path
import re, argparse

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',default='.');args=ap.parse_args();repo=Path(args.repo).resolve()
    main=repo/'app/main.py'
    if main.exists():
        t=main.read_text(encoding='utf-8')
        t=re.sub(r'\n?# === RECEIVABLES MODULE IMPORT ===\nfrom app\.routers import receivables\nimport app\.receivables_models as _receivables_models\n','\n',t)
        t=re.sub(r'\n?# === RECEIVABLES MODULE ROUTER ===\napp\.include_router\(receivables\.router\)\n','\n',t)
        main.write_text(t,encoding='utf-8')
    idx=repo/'app/static/index.html'
    if idx.exists():
        t=idx.read_text(encoding='utf-8')
        t=re.sub(r'\n?<!-- RECEIVABLES MODULE LAUNCHER -->\n<script src="/static/receivables-launcher\.js" defer></script>\n?','\n',t)
        idx.write_text(t,encoding='utf-8')
    for rel in ['app/receivables_models.py','app/routers/receivables.py','app/static/receivables.html','app/static/receivables.css','app/static/receivables.js','app/static/receivables-launcher.js','app/data/legacy_receivables_2026.json']:
        p=repo/rel
        if p.exists(): p.unlink()
    print('수납/미수금 코드 연결 제거 완료. DB의 신규 receivable_* 테이블은 안전을 위해 삭제하지 않았습니다.')
if __name__=='__main__':main()
