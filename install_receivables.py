#!/usr/bin/env python3
from __future__ import annotations
import argparse, shutil, sys, time, re, hashlib
from pathlib import Path

MARKER_IMPORT = "# === RECEIVABLES MODULE IMPORT ==="
MARKER_ROUTER = "# === RECEIVABLES MODULE ROUTER ==="
MARKER_LAUNCHER = "<!-- RECEIVABLES MODULE LAUNCHER -->"


def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def main():
    ap=argparse.ArgumentParser(description='member-management 수납/미수금 모듈 추가 설치')
    ap.add_argument('--repo',default='.',help='member-management 저장소 경로')
    ap.add_argument('--no-launcher',action='store_true',help='기존 메인화면에 수납/미수금 바로가기 버튼을 추가하지 않음')
    args=ap.parse_args()
    repo=Path(args.repo).resolve(); here=Path(__file__).resolve().parent; payload=here/'payload'
    main_py=repo/'app/main.py'; index_html=repo/'app/static/index.html'
    if not main_py.exists():
        raise SystemExit(f'ERROR: {main_py} 없음. member-management 저장소 루트에서 실행하세요.')
    stamp=time.strftime('%Y%m%d_%H%M%S'); backup=repo/'_receivables_backup'/stamp; backup.mkdir(parents=True,exist_ok=True)
    before_main=sha(main_py); before_index=sha(index_html)
    shutil.copy2(main_py,backup/'main.py')
    if index_html.exists(): shutil.copy2(index_html,backup/'index.html')

    # 1) 새 파일 추가: 기존 파일 덮어쓰기 없음(수납 모듈 전용 경로만 사용)
    copy_map=[
        ('app/receivables_models.py','app/receivables_models.py'),
        ('app/routers/receivables.py','app/routers/receivables.py'),
        ('app/static/receivables.html','app/static/receivables.html'),
        ('app/static/receivables.css','app/static/receivables.css'),
        ('app/static/receivables.js','app/static/receivables.js'),
        ('app/static/receivables-launcher.js','app/static/receivables-launcher.js'),
        ('app/data/legacy_receivables_2026.json','app/data/legacy_receivables_2026.json'),
    ]
    for src_rel,dst_rel in copy_map:
        src=payload/src_rel; dst=repo/dst_rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)

    # 2) main.py는 기존 코드를 삭제/변경하지 않고 import + include_router 두 블록만 추가
    text=main_py.read_text(encoding='utf-8')
    if MARKER_IMPORT not in text:
        block=f"\n{MARKER_IMPORT}\nfrom app.routers import receivables\nimport app.receivables_models as _receivables_models\n"
        # create_all보다 반드시 앞에 import되도록 app.models import 직후 삽입
        m=re.search(r'(^\s*import\s+app\.models\s+as\s+_models[^\n]*\n)',text,re.M)
        if m:
            text=text[:m.end()]+block+text[m.end():]
        else:
            # FastAPI app 생성 전 안전한 위치
            pos=text.find('app = FastAPI(')
            if pos<0: raise SystemExit('ERROR: main.py에서 FastAPI 생성 지점을 찾지 못했습니다. 변경하지 않았습니다.')
            text=text[:pos]+block+'\n'+text[pos:]
    if MARKER_ROUTER not in text:
        block=f"\n{MARKER_ROUTER}\napp.include_router(receivables.router)\n"
        # 기존 router include가 끝난 뒤 추가. admin include가 있으면 그 다음.
        m=re.search(r'(^\s*app\.include_router\(admin\.router[^\n]*\n)',text,re.M)
        if m:
            text=text[:m.end()]+block+text[m.end():]
        else:
            # static_dir 전에 추가
            pos=text.find('static_dir =')
            if pos<0: raise SystemExit('ERROR: main.py에서 router 삽입 지점을 찾지 못했습니다. 변경하지 않았습니다.')
            text=text[:pos]+block+'\n'+text[pos:]
    main_py.write_text(text,encoding='utf-8')

    # 3) 기존 index 기능은 건드리지 않고 스크립트 1줄만 추가(선택 가능)
    if index_html.exists() and not args.no_launcher:
        html=index_html.read_text(encoding='utf-8')
        if MARKER_LAUNCHER not in html:
            snippet=f'\n{MARKER_LAUNCHER}\n<script src="/static/receivables-launcher.js" defer></script>\n'
            if '</body>' in html.lower():
                idx=html.lower().rfind('</body>'); html=html[:idx]+snippet+html[idx:]
            else: html+=snippet
            index_html.write_text(html,encoding='utf-8')

    # 기록
    (backup/'INSTALL_INFO.txt').write_text(
        f'installed_at={stamp}\nmain_before={before_main}\nmain_after={sha(main_py)}\nindex_before={before_index}\nindex_after={sha(index_html)}\n',encoding='utf-8')
    print('OK: 수납/미수금 모듈 설치 완료')
    print(' - 기존 모델/라우터/회원등록/폐업 로직 수정 없음')
    print(' - 추가 URL: /receivables')
    print(f' - 원본 백업: {backup}')
    print(' - 다음: git diff 확인 → 테스트 → commit/push')

if __name__=='__main__': main()
