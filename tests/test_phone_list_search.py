"""화면용 연락처 분리와 회원 검색 회귀 테스트. 운영 DB 접속 없음."""
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import crud, models
from app.database import Base


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            models.LicenseHolder(name='가상가', management_number='신13-12', category='택배',
                                 region='동해시', vehicle_number='강원83배 1021호',
                                 mobile='010-1111-2222 010-3333-4444'),
            models.LicenseHolder(name='가상나', management_number='양22-93', category='개인',
                                 region='강릉시', vehicle_number='강원82자 2316호',
                                 mobile='010-5555-6666 010-7777-8888(대)',
                                 phone='033-123-4567', agent_mobile='010 8888 7777'),
        ])
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize('needle,expected', [
    ('010-1111-2222', '신13-12'),
    ('01011112222', '신13-12'),
    ('010-3333-4444', '신13-12'),
    ('01033334444', '신13-12'),
    ('010-7777-8888', '양22-93'),
    ('01077778888', '양22-93'),
    ('01055556666', '양22-93'),
    ('0331234567', '양22-93'),
    ('01088887777', '양22-93'),
    ('가상가', '신13-12'),
])
def test_either_stored_phone_can_find_member_with_or_without_hyphens(db, needle, expected):
    results, count = crud.get_region_vehicle_page(
        db, models.LicenseHolder, page=1, limit=50,
        search=needle, search_fields=['name','mobile','phone','agent_mobile'], nonempty_any=['name','vehicle_number'],
    )
    assert count == 1
    assert [r.management_number for r in results] == [expected]


def test_query_not_overmatching_other_member(db):
    for needle in ('01033334445', '01077778889'):
        rows, total = crud.get_list(db, models.LicenseHolder, search=needle,
                                    search_fields=['name','mobile','phone','agent_mobile'])
        assert total == 0 and rows == []


def test_display_keeps_unverified_annotation_and_does_not_merge_phones(tmp_path):
    js = (Path(__file__).parents[1] / 'app/static/app.js').read_text(encoding='utf-8')
    start = js.index('// ===== 연락처 복수 번호 분리 표시')
    end = js.index('// ===== 데이터 정합성 점검', start)
    snippet = js[start:end]
    # 프런트엔드의 실제 함수만 독립적으로 실행한다. 네트워크/브라우저/운영 DB 불필요.
    node_code = '''
const window={};
const e_=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const fv=s=>s||'-';
''' + snippet + '''
const plain=_phoneDisplayParts('010-1111-2222 010-3333-4444');
const marked=_phoneDisplayParts('010-5555-6666 010-7777-8888(대)');
const unclear=_phoneDisplayParts('010-5555-6666 010-7777-8888 설명필요');
const html=_phoneListCell('010-5555-6666 010-7777-8888(대)');
console.log(JSON.stringify({plain,marked,unclear,html}));
'''
    # Windows 환경에서 subprocess.run(..., capture_output=True)의 stdout가
    # None으로 돌아오는 사례가 있어, stdout를 임시 파일로 직접 수집한다.
    # Node 코드는 기존과 동일하게 실행하며 성공 여부와 JSON 결과를 검증한다.
    js_path = tmp_path / 'phone_display_test.cjs'
    out_path = tmp_path / 'phone_display_result.json'
    js_path.write_text(node_code, encoding='utf-8')
    with out_path.open('wb') as out_file:
        proc = subprocess.run(
            ['node', str(js_path)],
            stdout=out_file,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert proc.returncode == 0, (
        'Node 실행 오류: ' + (proc.stderr or b'').decode('utf-8', errors='replace')
    )
    assert out_path.stat().st_size > 0, 'Node가 JSON 결과를 출력하지 않았음'
    v = json.loads(out_path.read_text(encoding='utf-8'))
    assert [x['number'] for x in v['plain']] == ['010-1111-2222','010-3333-4444']
    assert [x['number'] for x in v['marked']] == ['010-5555-6666','010-7777-8888']
    assert v['marked'][1]['note'] == '(대)'
    assert v['unclear'] is None
    assert '010-7777-8888' in v['html'] and '(대)' in v['html']
    assert '대리인' not in v['html']
