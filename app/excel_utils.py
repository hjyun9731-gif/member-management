"""
엑셀 처리 유틸리티 v4.0
- 양도양수대장: 29개 연도별 시트 전부 처리 (예정자/택배예정자 제외)
- 모든 파일: 줄바꿈/공백 컬럼명 정규화
- 지역 정규화: '춘천' → '춘천시'
- 성명 정규화: '이 종 일' → '이종일'
- 날짜 연도 추출: data_year 자동 설정
"""
import re, io, logging
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 정규화 함수
# ─────────────────────────────────────────────

def _nc(col: str) -> str:
    s = str(col).replace('\n','').replace('\r','').replace('\t','')
    s = re.sub(r'\s+', '', s).replace('．', '.').replace('․', '.').strip()
    return s

def normalize_name(s: str) -> str:
    if not s: return s
    t = s.strip()
    if re.match(r'^[가-힣ㄱ-ㅎ](\s[가-힣ㄱ-ㅎ])+$', t):
        return t.replace(' ', '')
    return t

def _cv(v) -> str:
    if v is None: return ''
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)): return ''
    s = str(v).strip()
    if s.lower() in ('nan','none','null','nat','#n/a','n/a'): return ''
    return s.replace('\n',' ').replace('\r',' ').strip()

def normalize_membership_status(val: str) -> str:
    """가입/미가입 처리:
    - 날짜 형식(숫자/점/하이픈 포함)이 있으면 → '가입'
    - 빈칸, 공백, null, '미가입', '0' 등 → '미가입'
    - '가입'이 명시된 경우 → '가입'
    """
    if not val or not val.strip():
        return '미가입'
    v = val.strip()
    if v.lower() in ('nan', 'none', 'null', 'nat', '#n/a', 'n/a', '', '0', 'x', '-'):
        return '미가입'
    # 날짜 패턴이 있으면 가입으로 처리
    if re.search(r'\d{2,4}[\.\-/]\d{1,2}', v):
        return '가입'
    # 숫자만 있어도 날짜로 간주 (예: 엑셀 날짜 serial number)
    if re.match(r'^\d{5}$', v):
        return '가입'
    # 명시적 가입
    if '가입' in v and '미가입' not in v:
        return '가입'
    # 명시적 미가입
    if '미가입' in v or '미' in v:
        return '미가입'
    # 그 외 값이 있으면 가입으로 간주
    if v:
        return '가입'
    return '미가입'

def normalize_closure_type(val: str) -> str:
    """폐지 → 폐업으로 통일"""
    if not val:
        return val
    if val.strip() == '폐지':
        return '폐업'
    return val.strip()

def parse_date_sort(date_str: str):
    """날짜 문자열 → datetime (정렬용). 파싱 실패 시 datetime.min 반환."""
    from datetime import datetime
    if not date_str:
        return datetime.min
    s = str(date_str).strip().rstrip('.')
    # 4자리 연도: 2024.04.02 / 2024-04-02 / 2024. 4. 2
    m = re.search(r'(19[0-9]{2}|20[0-9]{2})\s*[\.\-/]\s*(\d{1,2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # 2자리 연도: 24.04.02 / 99.12.30 / 16. 6.28
    m = re.search(r'^(\d{2})\s*[\.\-/]\s*(\d{1,2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        try:
            return datetime(year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return datetime.min


def extract_year(date_str: str) -> Optional[int]:
    """날짜 문자열에서 연도 추출. '14. 7. 8.' → 2014, '99.12.30.' → 1999"""
    if not date_str: return None
    s = str(date_str).strip()
    m = re.search(r'(19[0-9]{2}|20[0-2][0-9])', s)
    if m: return int(m.group())
    m = re.match(r'^(\d{2})\s*[\.\-/년]', s)
    if m:
        yy = int(m.group(1))
        return (2000 + yy) if yy <= 30 else (1900 + yy)
    return None

def extract_sheet_year(sheet_name: str) -> Optional[int]:
    """시트 이름에서 연도 추출. '2000년도' → 2000, '00년' → 2000"""
    m = re.search(r'(19[0-9]{2}|20[0-9]{2})', sheet_name)
    if m: return int(m.group())
    m = re.search(r'^(\d{2})년', sheet_name)
    if m:
        yy = int(m.group(1))
        return (2000 + yy) if yy <= 30 else (1900 + yy)
    return None

# ─────────────────────────────────────────────
# 지역 정규화
# ─────────────────────────────────────────────

_REGION_MAP = {
    "춘천":"춘천시","원주":"원주시","강릉":"강릉시","동해":"동해시",
    "태백":"태백시","속초":"속초시","삼척":"삼척시","홍천":"홍천군",
    "횡성":"횡성군","영월":"영월군","평창":"평창군","정선":"정선군",
    "철원":"철원군","화천":"화천군","양구":"양구군","인제":"인제군",
    "고성":"고성군","양양":"양양군",
}
_REGION_FULL = set(_REGION_MAP.values())

def _normalize_region(val: str) -> str:
    if not val: return val
    s = val.strip().replace(' ','')
    if s in _REGION_FULL: return s
    if s in _REGION_MAP: return _REGION_MAP[s]
    for short, full in _REGION_MAP.items():
        if s.startswith(short): return full
    return val.strip()

_VALID_FUELS = {'경유','휘발유','lpg','lp가스','전기','하이브리드','cng','lng','가스','디젤','천연가스',
               '엘피지','액화석유가스','bev','ev','electric','가솔린','gasoline','diesel'}

def _is_valid_fuel(val: str) -> bool:
    """유종으로 유효한 값인지 검증. 차종 값("18,포터II...")이 들어가지 않게."""
    if not val or len(val) < 2: return False
    vl = val.lower().replace(' ','')
    if re.match(r'^\d{2}[,.]', val.strip()): return False
    if re.match(r'^\d', val.strip()): return False
    return any(f in vl for f in _VALID_FUELS)


# ─────────────────────────────────────────────
# 공통 유종 정규화 함수 (모든 라우터에서 사용)
# ─────────────────────────────────────────────
_VEH_NAMES = ['포터', '봉고', '트럭', '탑차', '냉동', '사다리', '픽업', '렉스턴', '트레일러']
_BAD_FUEL_START = re.compile(r'^[\d\.\,]')


def normalize_fuel(fuel: str) -> str:
    """유종 표준화: LPG/전기/경유/휘발유/기타 중 하나로 반환.
    빈값이거나 차종명이면 빈 문자열('')을 반환한다."""
    if not fuel:
        return ''
    f = str(fuel).strip()
    if not f or f in ('.', '-', 'nan', 'None', 'NaN', '#N/A'):
        return ''
    # 숫자·연식 시작 또는 차종명 → 제외
    if _BAD_FUEL_START.match(f):
        return ''
    if any(vn in f for vn in _VEH_NAMES):
        return ''
    fl = f.lower().replace(' ', '').replace('　', '')
    # 정확한 단어 매칭 우선
    if fl in ('lpg', 'lp', 'lp가스', '엘피지', '액화석유가스', '가스'):
        return 'LPG'
    if fl in ('전기', 'ev', 'bev', 'electric', '전기차'):
        return '전기'
    if fl in ('경유', '디젤', 'diesel'):
        return '경유'
    if fl in ('휘발유', '가솔린', 'gasoline', 'petrol', '가솔'):
        return '휘발유'
    # 부분 포함 매칭
    if 'lpg' in fl or '엘피지' in fl or '액화석유' in fl:
        return 'LPG'
    if 'lp가스' in fl or ('가스' in fl and '가솔린' not in fl):
        return 'LPG'
    if '전기' in fl:
        return '전기'
    if 'ev' in fl:
        return '전기'
    if '경유' in fl or '디젤' in fl or 'diesel' in fl:
        return '경유'
    if '휘발유' in fl or '가솔린' in fl or 'gasoline' in fl:
        return '휘발유'
    if '하이브리드' in fl or 'hybrid' in fl:
        return '기타'
    if 'cng' in fl or 'lng' in fl or '천연가스' in fl:
        return 'LPG'
    return '기타'


def _is_valid_company(val: str) -> bool:
    if not val or len(val) < 2 or len(val) > 40: return False
    if re.search(r'\d{2}\s*\.\s*\d{1,2}\s*\.', val): return False
    if re.match(r'^\d', val): return False
    if re.search(r'[동리로길][0-9\s\-]', val): return False
    return True

# ─────────────────────────────────────────────
# 컬럼 매핑 사전
# ─────────────────────────────────────────────

_CM = {
    '지역':'region','지역별':'region','시군별':'region','시.군별':'region',
    '관할':'region','관할지역':'region',
    '차량번호':'vehicle_number','자동차번호':'vehicle_number',
    '자동차등록번호':'vehicle_number','등록번호':'vehicle_number',
    '성명':'name','이름':'name','대표자':'name','차주명':'name',
    '대표자명':'name','기사성명':'name','운전자명':'name',
    '상호':'company_name','회사명':'company_name','상호명':'company_name',
    '주소':'address','주소지':'address','소재지':'address',
    '전화번호':'phone','전화':'phone','연락처':'phone',
    '핸드폰':'mobile','휴대폰':'mobile','휴대전화':'mobile',
    '휴대전화번호':'mobile','모바일':'mobile',
    '인가일자':'approval_date','허가일자':'approval_date','인가일':'approval_date',
    '자격증명발급일자':'certificate_issue_date',
    '자격증명발급일':'certificate_issue_date',
    '자격발급일':'certificate_issue_date',
    '자격증명발급번호':'certificate_number',
    '자격번호':'certificate_number','자격증번호':'certificate_number',
    '허가번호':'permit_number',
    '운전면허번호':'driver_license_number',
    '운전면허증번호':'driver_license_number',
    '운전면허':'driver_license_number','면허번호':'driver_license_number',
    '차종':'vehicle_type',
    '유종':'fuel_type','연료':'fuel_type',
    '사업자등록번호':'business_number','사업자번호':'business_number',
    '소속업체':'affiliated_company','택배사':'affiliated_company',
    '재허가':'reapproval_date','재허가일자':'reapproval_date','재허가일':'reapproval_date',
    '공문주소':'official_address','공문발송주소':'official_address',
    '대리인':'agent_name','대리인성명':'agent_name','대리인이름':'agent_name',
    '대리인주민등록번호':'agent_resident_number','대리인주민번호':'agent_resident_number',
    '대리인핸드폰':'agent_mobile','대리인핸드폰번호':'agent_mobile','대리인전화':'agent_mobile',
    '대리인휴대폰':'agent_mobile','대리인휴대전화':'agent_mobile',
    '가입일자':'membership_date','가입일':'membership_date',
    '주민등록번호':'resident_number','주민번호':'resident_number',
    '비고':'memo',
    '가입여부':'membership_status','가입/미가입':'membership_status',
    '회원구분':'membership_status',
    '개인/택배':'category',
    '관리번호':'management_number',
    '가입년도':'_skip','인가년도':'_skip','인가월':'_skip',
}

_TM = {
    **_CM,
    '번호':'seq_number',
    '접수일자':'receipt_date','접수일':'receipt_date',
    '양도자':'transferor','양도인':'transferor',
    '양도자성명':'transferor','양도인성명':'transferor','양도자명':'transferor',
    '성명(양도)':'transferor','양도(자)성명':'transferor',
    '양수자':'transferee','양수인':'transferee',
    '양수자성명':'transferee','양수인성명':'transferee','양수자명':'transferee',
    '성명(양수)':'transferee','양수(자)성명':'transferee',
    '장부정리':'ledger_update',
    '전산보고':'computer_report',
    '처리일자':'process_date','처리일':'process_date',
    '인가일자':'approval_date',   # 인가일자 = 관청 공문 발행일 (접수일자/처리일자와 다름)
}

_CLM = {
    **_CM,
    '번호':'management_number',
    '접수일자':'receipt_date',
    '처리구분':'closure_type','폐지구분':'closure_type',
    '데이터구분':'data_type','자료구분':'data_type',
    '처리일자':'closure_date','폐지일자':'closure_date',
    '사유':'reason',
    '이름':'name',
    '휴대폰':'mobile',
    '이전정보보기':'_skip','현재정보보기':'_skip',
}

_CHM = {
    **_CM,
    '번호':'seq_number',
    '접수일자':'receipt_date','신고일자':'receipt_date','등록일자':'receipt_date',
    '내용':'_change_content',
    '변경내용':'_change_content','변경사항':'_change_content',
    '변경유형':'change_type','구분':'change_type','변경종류':'change_type',
    '변경전':'before_value','변경 전':'before_value','이전주소':'before_value',
    '변경전주소':'before_value','변경전내용':'before_value','이전내용':'before_value',
    '이전':'before_value','전주소':'before_value','종전주소':'before_value',
    '변경후':'after_value','변경 후':'after_value','현재주소':'after_value',
    '변경후주소':'after_value','변경후내용':'after_value','현재내용':'after_value',
    '현재':'after_value','변경된내용':'after_value','신주소':'after_value','새주소':'after_value',
    '변경일자':'change_date','처리일자':'change_date','변경일':'change_date',
    '인가일자':'change_date',
}

_ALM = {
    '연도':'year','년도':'year','월':'month',
    '협회가입':'association_join','양도':'transfer_in',
    '타도':'other_region','폐지':'closed','탈퇴':'withdrawn',
    '택배신규':'delivery_new','관리비폐지':'mgmt_fee_closed',
    '70세':'over_70','협회기본대수':'base_count',
    '총부과대수':'total_count','택배관리':'delivery_mgmt',
}

FILE_MAPPINGS = {
    '면허자현황': _CM,
    '양도양수대장': _TM,
    '폐지현황': _CLM,
    '이전폐지현황': _CLM,
    '변경이력대장': _CHM,
    '주소변경등록대장': _CHM,   # 신규 통일 명칭
    '주소지변경대장': _CHM,     # 구 명칭 하위호환
    '변경등록대장': _CHM,
    '부과대수': _ALM,
}

# 예정자 관련 시트 제외 패턴
_SKIP_SHEET_PATTERNS = ['예정자', '택배예정자', 'summary', '요약', '집계']

# 이전폐지현황 시트명 → closure_type
_SHEET_CTYPE = {
    '타도이관':'이관','양도':'양도','폐지':'폐업',
    '사망':'사망','말소,기타':'말소','말소기타':'말소',
    '자격증취소자':'기타',
}

# 성명 계열 필드
_NAME_FIELDS = {'name','transferor','transferee'}

# ─────────────────────────────────────────────
# 변경이력 자동 분류
# ─────────────────────────────────────────────

# 변경유형 키워드 매핑 (공백/특수문자 제거 후 포함 여부로 매칭)
_CK = {
    '구조변경':          ['구조변경', '구조변 경', '구조 변경'],
    '전속계약 업체변경': ['전속계약업체변경', '전속계약업체', '전속업체변경', '전속업체', '전속계약', '소속업체변경', '업체변경', '전속변경'],
    '주소지변경':        ['주소지변경', '주소변경', '주소이전', '이전주소'],
    '상호변경':          ['상호변경'],
    '등록이관':          ['등록이관', '이관등록'],
    '이전전출':          ['이전전출', '전출'],
    '대표자변경':        ['대표자변경'],
    '성명변경':          ['성명변경', '이름변경'],
    '번호변경':          ['번호변경', '차량번호변경', '번호판변경'],
    '양도':              ['양도양수', '양도'],
    '폐업':              ['폐업', '폐지'],
    '이관':              ['이관'],
}

def _normalize_text(s: str) -> str:
    """공백/특수문자/줄바꿈 완전 제거 후 소문자 변환"""
    return re.sub(r'[\s\r\n\t\-_·,./()（）\[\]【】]+', '', str(s or '')).lower()

def _detect_ctype(active_cols, row):
    active_vals = {c: row.get(c,'') for c in active_cols if row.get(c,'').strip()}
    hay_raw = (' '.join(active_vals.keys()) + ' ' + ' '.join(active_vals.values()))
    hay = _normalize_text(hay_raw)
    for ct, kws in _CK.items():
        if any(_normalize_text(kw) in hay for kw in kws):
            return ct
    return '기타'

def _parse_change_text(text: str):
    text = text.strip().replace('\r','')
    ct, bv, av = '기타', '', ''
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    first = lines[0] if lines else text
    first_norm = _normalize_text(first)
    for _ct, kws in _CK.items():
        if any(_normalize_text(kw) in first_norm for kw in kws):
            ct = _ct; break
    m = re.search(r'(.+?)\s*[-→>]+\s*(.+)', text)
    if m: bv, av = m.group(1).strip(), m.group(2).strip()
    elif len(lines) > 1: bv = '\n'.join(lines[1:])
    return ct, bv, av

# ─────────────────────────────────────────────
# 헤더 행 자동 감지
# ─────────────────────────────────────────────

def _find_header(df_raw, mapping):
    known = {_nc(k) for k in mapping}
    best, best_score = 0, 0
    for i in range(min(8, len(df_raw))):
        vals = [_nc(str(v)) for v in df_raw.iloc[i]
                if str(v).strip() not in ('','nan','None')]
        score = sum(1 for v in vals if v in known)
        if score > best_score: best_score, best = score, i
    return best if best_score >= 1 else 0

def _read_df(content: bytes, mapping: dict, sheet=0) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(content), engine='openpyxl', header=None, dtype=str, sheet_name=sheet)
    hrow = _find_header(raw, mapping)
    df = pd.read_excel(io.BytesIO(content), engine='openpyxl', header=hrow, dtype=str, sheet_name=sheet)
    df = df.fillna('')
    df.columns = [str(c) for c in df.columns]
    return df

def _col_map(df, mapping):
    mapped, unmapped = {}, []
    for col in df.columns:
        nk = _nc(col)
        if nk in mapping: mapped[col] = mapping[nk]
        else:
            found = any((mk in nk or nk in mk) and len(nk) > 1 for mk in mapping
                        if (mapped.__setitem__(col, mapping[mk]) or True) and False)
            if not found:
                for mk, mv in mapping.items():
                    if mk and len(nk) > 1 and (mk in nk or nk in mk):
                        mapped[col] = mv; found = True; break
            if not found: unmapped.append(col)
    return mapped, unmapped

def _df_to_records(df, mapping, file_type='', extra=None):
    cmap, _ = _col_map(df, mapping)
    cols = df.columns.tolist()
    records = []
    for _, row in df.iterrows():
        rec, raw = {}, {}
        for col in cols:
            orig = _cv(row.get(col,''))
            raw[col] = orig
            if col not in cmap: continue
            field = cmap[col]
            if field == '_skip': continue
            if field.startswith('_'): rec[field] = orig; continue
            if field in rec and rec[field]: continue
            if field in _NAME_FIELDS:
                rec[field] = normalize_name(orig)
            elif field == 'region':
                rec[field] = _normalize_region(orig)
            elif field == 'affiliated_company':
                if _is_valid_company(orig): rec[field] = orig
            elif field == 'fuel_type':
                # 유종 검증: 차종 값("18,포터II...")이 들어가지 않게
                if orig and _is_valid_fuel(orig):
                    rec[field] = orig
            elif field == 'membership_status':
                rec[field] = normalize_membership_status(orig)
            elif field == 'closure_type':
                rec[field] = normalize_closure_type(orig)
            else:
                rec[field] = orig

        if file_type in ('변경이력대장','주소변경등록대장','주소지변경대장','변경등록대장'):
            ct_text = rec.pop('_change_content','')
            if ct_text:
                ct, bv, av = _parse_change_text(ct_text)
                rec.setdefault('change_type', ct)
                rec.setdefault('before_value', bv)
                rec.setdefault('after_value', av)
            if file_type in ('주소지변경대장', '주소변경등록대장'):
                rec['change_type'] = '주소지변경'
                if rec.get('before_value') and not rec.get('after_value'):
                    rec['after_value'] = rec['before_value']
                    rec['before_value'] = ''
            elif not rec.get('change_type'):
                active = [c for c in cols if raw.get(c,'').strip()]
                rec['change_type'] = _detect_ctype(active, raw)
            # 처리일자(change_date) 없으면 접수일자(receipt_date)로 대체
            if not rec.get('change_date') and rec.get('receipt_date'):
                rec['change_date'] = rec['receipt_date']

        if extra:
            for k, v in extra.items():
                rec.setdefault(k, v)
        rec['raw_data'] = raw
        records.append(rec)
    return records

# ─────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────

def excel_to_records(content: bytes, file_type: str,
                     preview: bool = False, preview_n: int = 10):
    mapping = FILE_MAPPINGS.get(file_type, _CM)

    # 면허자현황: 개인/택배 시트
    if file_type == '면허자현황':
        recs, cmap, un = _read_member_sheets(content, preview, preview_n)
        return recs, cmap, un, []

    # 양도양수대장: 연도별 시트 ALL
    if file_type == '양도양수대장':
        recs, cmap, un = _read_transfer_all_sheets(content, preview, preview_n)
        return recs, cmap, un, []

    # 이전폐지현황: 폐지유형별 시트
    if file_type == '이전폐지현황':
        recs, cmap, un = _read_prev_closure_sheets(content, preview, preview_n)
        return recs, cmap, un, []

    # 변경이력대장/주소지변경대장/변경등록대장: 연도별 멀티시트 ALL
    if file_type in ('변경이력대장','주소변경등록대장','주소지변경대장','변경등록대장'):
        recs, cmap, un, slogs = _read_change_all_sheets(content, file_type, mapping, preview, preview_n)
        return recs, cmap, un, slogs

    # 단일 시트
    df = _read_df(content, mapping)
    cmap, unmapped = _col_map(df, mapping)
    if preview: df = df.head(preview_n)
    records = _df_to_records(df, mapping, file_type)
    return records, cmap, unmapped, []


def _should_skip_sheet(sheet_name: str) -> bool:
    return any(p.lower() in sheet_name.lower() for p in _SKIP_SHEET_PATTERNS)


def _read_transfer_all_sheets(content: bytes, preview: bool, preview_n: int):
    """양도양수대장: 연도별 시트 ALL 읽기 (예정자/택배예정자 제외)

    실제 파일 구조 기반:
    - 시트명: '2000년도' ~ '2026년도' (4자리 연도)
    - 헤더: '접수\\n일자', '주[공백]소', '자격증명\\n발급일자' 등 줄바꿈/공백 포함
    - 번호 컬럼: int형 정수 (1, 2, 3, ...)
    - 관리번호: 양YY-번호  (예: 양26-1, 양26-28, 양00-15)
    - 접수일자: 2000년도 등 구버전 시트에는 없을 수 있음
    - 인가일자 ≠ 접수일자 (절대 혼용 금지)
    """
    import warnings; warnings.filterwarnings('ignore')
    xl = pd.ExcelFile(io.BytesIO(content), engine='openpyxl')
    all_rec, all_cmap, all_un = [], {}, []

    def _nc_col(c):
        """컬럼명 정규화: 줄바꿈/다중공백 제거"""
        return re.sub(r'[\s\n\r]+', '', str(c)).strip()

    # 정규화된 컬럼명 → DB 필드 매핑
    COL_MAP = {
        '번호':            'seq_number',
        '접수일자':         'receipt_date',
        '지역별':          'region',
        '차량번호':         'vehicle_number',
        '양도자':          'transferor',
        '양수자':          'transferee',
        '주민등록번호':     'resident_number',
        '주소':            'address',
        '전화번호':         'phone',
        '핸드폰':          'mobile',
        '인가일자':         'approval_date',
        '가입일자':         'membership_date',
        '자격증명발급일자':  'certificate_issue_date',
        '자격증명발급번호':  'certificate_number',
        '장부정리':         'ledger_update',
        '운전면허번호':     'driver_license_number',
        '전산보고':         'computer_report',
        '비고':            'memo',
    }

    _NONE_VALS = {'nan', 'none', 'nat', '', '-', 'x'}

    for sheet in xl.sheet_names:
        if _should_skip_sheet(sheet):
            logger.info(f"[양도양수대장] SKIP 시트: {sheet}")
            continue

        # 시트명에서 4자리 연도 추출
        m = re.search(r'(\d{4})', sheet)
        if not m:
            logger.warning(f"[양도양수대장] 연도 파싱 실패, SKIP: {sheet}")
            continue
        sheet_year = int(m.group(1))
        yy = str(sheet_year % 100).zfill(2)

        try:
            df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet,
                                   header=0, engine='openpyxl')
            if preview:
                df_raw = df_raw.head(preview_n)

            # 컬럼명 정규화
            norm_cols = [_nc_col(c) for c in df_raw.columns]
            df_raw.columns = norm_cols

            # cmap 기록
            for nc in norm_cols:
                if nc in COL_MAP and nc != '번호':
                    all_cmap[nc] = COL_MAP[nc]
                elif not nc.startswith('Unnamed') and nc not in all_un and nc:
                    all_un.append(nc)

            # 이 시트에 접수일자 컬럼이 있는지
            has_receipt = '접수일자' in norm_cols

            valid_recs = []
            for _, row in df_raw.iterrows():
                seq_raw = row.get('번호', None)
                if seq_raw is None or (isinstance(seq_raw, float) and pd.isna(seq_raw)):
                    continue
                try:
                    seq_int = int(float(str(seq_raw).strip()))
                except (ValueError, TypeError):
                    continue
                if seq_int <= 0:
                    continue

                rec = {
                    'management_number': f"양{yy}-{seq_int}",
                    'seq_number':        str(seq_int),
                    'sheet_year':        sheet_year,
                    'data_year':         sheet_year,
                }

                for nc, field in COL_MAP.items():
                    if nc == '번호':
                        continue
                    val = row.get(nc, None)
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        rec[field] = None
                        continue
                    s = str(val).strip().rstrip('.')
                    if s.lower() in _NONE_VALS:
                        rec[field] = None
                    else:
                        rec[field] = s

                # 접수일자 없는 시트는 명시적으로 None
                if not has_receipt:
                    rec['receipt_date'] = None

                # raw_data 보존
                rec['raw_data'] = {
                    nc: (None if (isinstance(v, float) and pd.isna(v)) else str(v).strip())
                    for nc, v in zip(norm_cols, row)
                    if nc and not nc.startswith('Unnamed')
                }

                valid_recs.append(rec)

            logger.info(f"[양도양수대장] '{sheet}': {len(valid_recs)}행 "
                        f"(year={sheet_year}, yy={yy}, 접수일자={'있음' if has_receipt else '없음'})")
            all_rec.extend(valid_recs)

        except Exception as e:
            logger.error(f"[양도양수대장] 시트 '{sheet}' 오류: {e}")
            continue

    logger.info(f"[양도양수대장] 전체 합계: {len(all_rec)}행")
    return all_rec, all_cmap, all_un

def _read_member_sheets(content: bytes, preview: bool, preview_n: int):
    import warnings; warnings.filterwarnings('ignore')
    xl = pd.ExcelFile(io.BytesIO(content), engine='openpyxl')
    targets = [s for s in xl.sheet_names if s in ('개인','택배')]
    if not targets: targets = xl.sheet_names[:1]
    all_rec, all_cmap, all_un = [], {}, []
    for sheet in targets:
        try:
            df = _read_df(content, _CM, sheet)
            if preview: df = df.head(preview_n)
            cmap, un = _col_map(df, _CM)
            all_cmap.update(cmap)
            for u in un:
                if u not in all_un: all_un.append(u)
            recs = _df_to_records(df, _CM, '면허자현황')
            # ★ 시트 이름이 '개인' 또는 '택배'이면 category를 강제 설정
            force_cat = sheet if sheet in ('개인', '택배') else None
            for r in recs:
                if force_cat:
                    r['category'] = force_cat  # 엑셀 컬럼값 덮어쓰기
                y = extract_year(r.get('approval_date',''))
                if y: r['data_year'] = y
            logger.info(f"[면허자현황] 시트 '{sheet}': {len(recs)}행 (category={force_cat})")
            all_rec.extend(recs)
        except Exception as e:
            logger.error(f"[면허자현황] 시트 '{sheet}' 오류: {e}")
    return all_rec, all_cmap, all_un


def _read_prev_closure_sheets(content: bytes, preview: bool, preview_n: int):
    import warnings; warnings.filterwarnings('ignore')
    xl = pd.ExcelFile(io.BytesIO(content), engine='openpyxl')
    all_rec, all_cmap, all_un = [], {}, []
    for sheet in xl.sheet_names:
        ct = _SHEET_CTYPE.get(sheet, '기타')
        try:
            df = _read_df(content, _CLM, sheet)
            if len(df) == 0: continue
            if preview: df = df.head(preview_n)
            cmap, un = _col_map(df, _CLM)
            all_cmap.update(cmap)
            for u in un:
                if u not in all_un: all_un.append(u)
            extra = {'closure_type': ct, 'data_type': '이전자료'}
            recs = _df_to_records(df, _CLM, '이전폐지현황', extra)
            for r in recs:
                y = extract_year(r.get('closure_date',''))
                if y: r['data_year'] = y
            logger.info(f"[이전폐지현황] 시트 '{sheet}': {len(recs)}행 (구분={ct})")
            all_rec.extend(recs)
        except Exception as e:
            logger.error(f"[이전폐지현황] 시트 '{sheet}' 오류: {e}")
    return all_rec, all_cmap, all_un


def _read_change_all_sheets(content: bytes, file_type: str, mapping: dict,
                             preview: bool, preview_n: int):
    """변경이력대장/주소지변경대장/변경등록대장: 모든 시트 전부 읽기
    - 예정자/택배예정자 시트만 제외
    - 숨김 시트 포함 (openpyxl read_only=False로 처리)
    - 헤더행 최대 20행까지 탐색
    - 시트별 처리 건수/헤더위치/매핑컬럼/오류 로그 출력
    """
    import warnings; warnings.filterwarnings('ignore')
    xl = pd.ExcelFile(io.BytesIO(content), engine='openpyxl')
    all_rec, all_cmap, all_un, sheet_logs = [], {}, [], []
    total_sheets = len(xl.sheet_names)
    logger.info(f"[{file_type}] 총 시트 수: {total_sheets}")

    for sheet in xl.sheet_names:
        if _should_skip_sheet(sheet):
            logger.info(f"[{file_type}] SKIP 시트: {sheet}")
            sheet_logs.append({'sheet': sheet, 'count': 0, 'status': 'skip'})
            continue

        sheet_year = extract_sheet_year(sheet)
        try:
            # 헤더 최대 20행까지 탐색
            raw = pd.read_excel(io.BytesIO(content), engine='openpyxl',
                                header=None, dtype=str, sheet_name=sheet)
            if len(raw) == 0:
                sheet_logs.append({'sheet': sheet, 'count': 0, 'status': 'empty'})
                logger.info(f"[{file_type}] 시트 '{sheet}': 빈 시트")
                continue

            known = {_nc(k) for k in mapping}
            best_row, best_score = 0, 0
            for i in range(min(20, len(raw))):
                vals = [_nc(str(v)) for v in raw.iloc[i] if str(v).strip() not in ('', 'nan', 'None')]
                score = sum(1 for v in vals if v in known)
                if score > best_score:
                    best_score, best_row = score, i

            hrow = best_row if best_score >= 1 else 0
            df = pd.read_excel(io.BytesIO(content), engine='openpyxl',
                               header=hrow, dtype=str, sheet_name=sheet)
            df = df.fillna('')
            df.columns = [str(c) for c in df.columns]

            # 데이터 행 존재 여부 확인
            data_rows = df[df.apply(lambda r: any(str(v).strip() not in ('', 'nan') for v in r), axis=1)]
            if len(data_rows) == 0:
                sheet_logs.append({'sheet': sheet, 'count': 0, 'status': 'no_data'})
                logger.info(f"[{file_type}] 시트 '{sheet}': 데이터 행 없음")
                continue

            if preview:
                df = df.head(preview_n)

            cmap, un = _col_map(df, mapping)
            all_cmap.update(cmap)
            for u in un:
                if u not in all_un:
                    all_un.append(u)

            extra = {'sheet_year': sheet_year}
            if file_type in ('주소지변경대장', '주소변경등록대장'):
                extra['change_type'] = '주소지변경'

            recs = _df_to_records(df, mapping, file_type, extra)
            valid_recs = [r for r in recs
                          if r.get('vehicle_number') or r.get('name') or
                             r.get('after_value') or r.get('before_value')]

            mapped_cols = [f"{k}→{v}" for k, v in cmap.items() if v not in ('_skip',)][:8]
            log_msg = (f"[{file_type}] 시트 '{sheet}' (연도={sheet_year}): "
                       f"헤더={hrow}행, 매핑={len(cmap)}컬럼, 유효={len(valid_recs)}건")
            logger.info(log_msg)
            sheet_logs.append({
                'sheet': sheet, 'year': sheet_year, 'count': len(valid_recs),
                'header_row': hrow, 'mapped': len(cmap), 'status': 'ok',
                'mapped_cols': ', '.join(mapped_cols)
            })
            all_rec.extend(recs)

        except Exception as e:
            err_msg = str(e)[:80]
            logger.error(f"[{file_type}] 시트 '{sheet}' 오류: {err_msg}")
            sheet_logs.append({'sheet': sheet, 'count': 0, 'status': f'error: {err_msg}'})
            continue

    # 아무 결과도 없으면 첫 번째 시트 단일 처리 시도
    if not all_rec and xl.sheet_names:
        first = xl.sheet_names[0]
        try:
            df = _read_df(content, mapping, first)
            if preview:
                df = df.head(preview_n)
            cmap, un = _col_map(df, mapping)
            extra = {}
            if file_type in ('주소지변경대장', '주소변경등록대장'):
                extra['change_type'] = '주소지변경'
            all_rec = _df_to_records(df, mapping, file_type, extra)
            all_cmap = cmap; all_un = un
            sheet_logs.append({'sheet': f'{first}(단독)', 'count': len(all_rec), 'status': 'ok'})
            logger.info(f"[{file_type}] 단독시트 처리: {len(all_rec)}행")
        except Exception as e:
            logger.error(f"[{file_type}] 단독시트 처리 오류: {e}")

    logger.info(f"[{file_type}] 전체 합계: {len(all_rec)}행 / {len(sheet_logs)}시트 처리")
    return all_rec, all_cmap, all_un, sheet_logs


def records_to_excel(records: list, exclude: list = None) -> bytes:
    if not records: return b''
    ex = set(exclude or ['raw_data','deleted_at','data_year'])
    rows = [{k: v for k, v in r.items() if k not in ex} for r in records]
    df = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False)
    return out.getvalue()


def mgmt_sort_key(mgmt: str) -> tuple:
    """관리번호 자연정렬 키: 연도+번호 기준 (90~99=1990~1999, 00~현재=2000~현재).
    예: '신26-181' → (2026, 181), '폐-80' → (9999, 80), '26-099' → (2026, 99)
    개인/택배 구분 없이 동일 기준 적용.
    빈값/None은 내림차순 시 맨 아래로 (0, 0) 반환.
    """
    from datetime import datetime as _dt
    cur_yy = _dt.now().year % 100  # 예: 2026 → 26

    # 빈값/None → 정렬 맨 아래 (내림차순 기준 가장 작은 값)
    if not mgmt or not str(mgmt).strip():
        return (0, 0, '')

    s = str(mgmt).strip()

    # 한글접두어 + 2자리연도 + 번호: 신26-181, 양26-001
    m = re.match(r'^[가-힣]*(\d{2})\s*[-]\s*(\d+)', s)
    if m:
        yy = int(m.group(1))
        # 현재 연도 기준: cur_yy 이하면 2000년대, 초과면 1900년대
        year = 2000 + yy if yy <= cur_yy else 1900 + yy
        return (year, int(m.group(2)), s)
    # 한글접두어만 있는 형태: 폐-80, 양-28
    m = re.match(r'^[가-힣]+[-]\s*(\d+)', s)
    if m:
        return (9999, int(m.group(1)), s)
    # 숫자만
    m = re.match(r'^(\d+)', s)
    if m:
        return (9998, int(m.group(1)), s)
    return (0, 0, s)  # 인식 불가 → 맨 아래
