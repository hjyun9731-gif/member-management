"""Real Chromium + local API + disposable DB smoke test. Never uses production URLs.

Run: python tests/browser_integrity_smoke.py --out /tmp/mm-browser
Optional: MM_TEST_POSTGRES_URL (validated by db_support).
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_support import postgres_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--serve-only', action='store_true', help='Serve disposable data for an attached browser')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mm_browser_') as temp:
        pg = postgres_url()
        os.environ['DATABASE_URL'] = pg.render_as_string(hide_password=False) if pg else f'sqlite:///{temp}/browser.sqlite'
        os.environ['SECRET_KEY'] = secrets.token_hex(32)
        os.environ.pop('ENABLE_VERIFIED_DATA_CORRECTIONS', None)
        from app.main import app
        from app import models, certificate_ledger_models as clm
        from app.database import Base, engine, SessionLocal
        from app.auth import get_password_hash
        Base.metadata.create_all(engine)
        password = 'Synthetic-Browser-Check-Only' if args.serve_only else secrets.token_urlsafe(20)
        with SessionLocal() as db:
            db.add(models.User(username='browser-test', role='admin', full_name='검증관리자', password_hash=get_password_hash(password)))
            db.add_all([
                models.LicenseHolder(id=101, name='가상검증A', management_number='양26-0100', registration_type='양도양수',
                    region='춘천시', vehicle_number='강원80바1101', category='개인', status='active', address='검증전주소',
                    mobile='010-1111-2222 010-3333-4444', certificate_number='26-101'),
                models.LicenseHolder(id=102, name='가상검증B', management_number='양26-100', registration_type='양도양수',
                    region='원주시', vehicle_number='강원80바1102', category='개인', status='active', mobile='확인필요'),
                models.Candidate(id=201, name='가상검증A', management_number='양26-0100', member_id=101,
                    is_registered=True, vehicle_number='강원80바1101', certificate_number='26-101'),
                models.MemberAgent(member_id=101, name='가상대리인', mobile='010-3333-4444', status='현재'),
                clm.CertificateIssuanceLedger(id=301, name='가상검증A', member_id=101, candidate_id=201,
                    document_number='26-101', vehicle_number='강원80바1101', status='발급완료'),
            ])
            db.commit()
        def snapshot():
            with engine.connect() as conn:
                return {t.name: sorted([tuple(str(v) for v in row) for row in conn.execute(t.select())]) for t in Base.metadata.sorted_tables}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        log = (out / 'server.log').open('w')
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--lifespan', 'off'], cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log)
        errors = []
        checks = []
        try:
            import httpx
            for _ in range(100):
                try:
                    if httpx.get(base + '/login', trust_env=False).status_code == 200: break
                except httpx.ConnectError: pass
                time.sleep(0.1)
            else: raise RuntimeError('Local server did not start')
            if args.serve_only:
                print(base, flush=True)
                server.wait()
                return
            from playwright.sync_api import sync_playwright, expect
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('MM_CHROMIUM_EXECUTABLE', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'] + shlex.split(os.environ.get('MM_CHROMIUM_ARGS', '')))
                page = browser.new_page(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base + '/login')
                page.locator('#username').fill('browser-test'); page.locator('#password').fill(password)
                page.locator('#password').press('Enter')
                expect(page.locator('#catNav')).to_be_visible()
                checks.append('실제 로그인')
                before = snapshot()
                page.get_by_role('button', name='보고/집계', exact=True).click()
                page.get_by_text('데이터 정합성 점검', exact=True).click()
                expect(page.locator('#diList table')).to_be_visible()
                assert page.locator('#diCode option').count() == 16
                expect(page.locator('#diFixApply')).to_be_disabled()
                expect(page.locator('#diCertApply')).to_be_disabled()
                checks.append('DQ-01~15 표시 / 정정 실행 기본 차단')
                page.locator('#diCode').select_option('DQ-01')
                page.locator('#diName').fill('가상검증A')
                page.locator('#diRegion').fill('춘천')
                page.locator('#diStatus').select_option('active')
                with page.expect_response(lambda r: '/findings?' in r.url and r.status == 200):
                    page.locator('#diSearch').click()
                expect(page.locator('#diList tbody tr')).to_have_count(1)
                expect(page.locator('#diList')).to_contain_text('가상검증A')
                page.screenshot(path=str(out / 'dq-filter.png'), full_page=True)
                with page.expect_download() as download_info:
                    page.locator('#diCsv').click()
                path = out / 'filtered.csv'; download_info.value.save_as(path)
                rows = list(csv.reader(io.StringIO(path.read_text('utf-8-sig'))))
                assert len(rows) == 2 and rows[1][2] == '가상검증A'
                checks.append('지역·상태·성명·유형 필터 및 동일 결과 CSV')
                page.locator('#diNum').fill('양26-100'); page.locator('#diDiag').click()
                expect(page.locator('#diDiagOut')).to_contain_text('양26-0100')
                checks.append('제로 패딩 관리번호 점유 진단')
                page.locator('#diCertLedger').fill('301'); page.locator('#diCertTarget').fill('101')
                page.locator('#diCertPreview').click()
                expect(page.locator('#diCertOut')).to_contain_text('비활성화')
                expect(page.locator('#diCertApply')).to_be_disabled()
                page.locator('#diCertList').click()
                expect(page.locator('#diCertOut')).to_contain_text('미연결 후보 전체')
                checks.append('자격증명 재연결 미리보기·후보 조회 / 쓰기 차단')
                assert snapshot() == before
                checks.append('진단·미리보기·내보내기 후 전체 테이블 원본 보존')
                page.get_by_role('button', name='회원관리', exact=True).click()
                page.get_by_text('개인회원', exact=True).first.click()
                expect(page.locator('#individualTbl table')).to_be_visible()
                page.locator('#individualSrch').fill('01033334444'); page.locator('#individualSrchBtn').click()
                expect(page.locator('#individualTbl tbody tr')).to_have_count(1)
                page.locator('#individualTbl').get_by_role('button', name='수정', exact=True).click()
                expect(page.locator('#mForm [data-mobile-part]')).to_have_count(2)
                page.locator('#mForm [name=address]').fill('브라우저검증새주소')
                with page.expect_response(lambda r: '/api/members/101' in r.url and r.request.method == 'PUT') as saved:
                    page.locator('#_mSave').click()
                assert saved.value.status == 200, saved.value.text()
                expect(page.locator('#modalBg')).not_to_be_visible()
                with SessionLocal() as db:
                    member = db.get(models.LicenseHolder, 101)
                    assert member.address == '브라우저검증새주소'
                    assert member.mobile == '010-1111-2222 010-3333-4444'
                    row = db.get(clm.CertificateIssuanceLedger, 301)
                    assert row.status == '발급완료' and row.member_id == 101 and row.candidate_id == 201
                    assert db.query(clm.CertificateIssuanceLedger).count() == 1
                checks.append('대리인 전화번호 검색·복수번호 수정 폼·주소 저장 / 발급완료 이력 보존')
                page.locator('#individualTbl').get_by_role('button', name='수정', exact=True).click()
                expect(page.locator('#mForm [name=address]')).to_have_value('브라우저검증새주소')
                page.screenshot(path=str(out / 'member-saved.png'), full_page=True)
                checks.append('저장 후 재조회 확인')
                assert not errors, errors
                browser.close()
            (out / 'result.json').write_text(json.dumps({'dialect': engine.dialect.name, 'checks': checks, 'page_errors': errors}, ensure_ascii=False, indent=2))
            print(json.dumps({'dialect': engine.dialect.name, 'passed': len(checks), 'checks': checks}, ensure_ascii=False))
        finally:
            server.terminate(); server.wait(timeout=15); log.close(); engine.dispose()


if __name__ == '__main__':
    main()
