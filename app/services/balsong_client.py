"""발송닷컴(balsong.com) SMS/LMS/MMS API 클라이언트.

계정 ID/비밀번호는 Railway 환경변수(BALSONG_USERNAME / BALSONG_PASSWORD)에서만
읽는다. 절대 소스코드에 하드코딩하거나 로그/응답에 그대로 출력하지 않는다.

아래 구현은 사용자가 전달한 "발송닷컴 SMS/LMS API 확정값" 문서를 그대로 따른다.
문서에 없는 동작(예: 별도 취소 API, JSON 바디 전송 등)은 추측해서 만들지 않았다.

- Base URL: https://balsong.com/Linkage/API/  (기능은 Service + Type 조합으로 구분, endpoint 1개)
- 요청 형식: POST, multipart/form-data, UTF-8 (문서의 실제 HTML 발송 예제 기준)
- 인증: 헤더가 아니라 POST 필드 UserID / UserPW
- 발송 파라미터: Service(SMS/LMS/MMS), Type=Send, Callback(발신번호), Subject, Main_Text,
  Destination(JSON 배열, 여러 수신자를 한 번에 묶어서 전달), Send_Date(예약, "YYYY-MM-DD HH:MM")
- 성공 판정: 응답 Result == "OK" and Code == 0  (성공 시 Job_No 반환)
- 결과조회: Type=Report(전체목록) / Type=Report_Detail(Job_No 기준 상세)
- 사용량조회: Service=TRAFFIC, Type=List
- 호출 제한: 문서에 "1초에 3회 이상 시도 시 10분간 접속 차단"이 명시되어 있어,
  수신자별로 반복 호출하지 않고 Destination 배열로 일괄 발송하며, 그 외 호출도
  이 클라이언트 내부에서 최소 간격을 두어 제한에 걸리지 않도록 한다.
"""
import os
import json
import time
import asyncio
import httpx
from typing import Optional, List, Dict, Any

BASE_URL = "https://balsong.com/Linkage/API/"

# 문서상 제한: 1초 3회 이상 → 10분 차단. 여유를 두고 최소 호출 간격을 둔다.
_MIN_CALL_INTERVAL_SEC = 0.5
_last_call_lock = asyncio.Lock()
_last_call_ts = 0.0


class BalsongClient:
    def __init__(self):
        self.username = os.getenv("BALSONG_USERNAME", "")
        self.password = os.getenv("BALSONG_PASSWORD", "")

    def _ok(self) -> bool:
        return bool(self.username and self.password)

    def _auth_fields(self) -> Dict[str, Any]:
        return {"UserID": self.username, "UserPW": self.password}

    async def _throttle(self):
        global _last_call_ts
        async with _last_call_lock:
            now = time.monotonic()
            wait = _MIN_CALL_INTERVAL_SEC - (now - _last_call_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call_ts = time.monotonic()

    async def _post(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """문서의 실제 발송 예제(method=Post, enctype=multipart/form-data)를 그대로 따른다.
        파일이 없는 일반 필드도 (None, value) 형태로 감싸서 httpx가 multipart로
        인코딩하도록 강제한다."""
        if not self._ok():
            return {"Result": "ERROR", "Code": "NO_CREDENTIALS",
                    "Message": "BALSONG_USERNAME / BALSONG_PASSWORD 환경변수가 설정되지 않았습니다."}

        await self._throttle()

        multipart_fields = []
        for key, value in fields.items():
            if value is None:
                continue
            multipart_fields.append(
                (key, (None, str(value).encode("utf-8"), "text/plain; charset=utf-8"))
            )
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(BASE_URL, files=multipart_fields)
            try:
                return r.json()
            except Exception:
                return {"Result": "ERROR", "Code": f"HTTP_{r.status_code}",
                        "Message": r.text[:300]}
        except Exception as e:
            return {"Result": "ERROR", "Code": "EXCEPTION", "Message": str(e)}

    async def test_connection(self) -> Dict[str, Any]:
        """계정 정보 유무만 확인. 부작용 없는 사용량조회(TRAFFIC/List) 호출로 실제 인증까지 검증."""
        if not self._ok():
            return {"ok": False, "has_credentials": False,
                    "message": "BALSONG_USERNAME / BALSONG_PASSWORD 환경변수가 설정되지 않았습니다."}
        from datetime import date
        today = date.today()
        raw = await self.get_usage(today.year, today.month)
        ok = raw.get("Result") == "OK"
        return {"ok": ok, "has_credentials": True, "raw": raw}

    async def send_message(self, *, service: str, callback: str, main_text: str,
                            destinations: List[Dict[str, Any]],
                            subject: Optional[str] = None,
                            send_date: Optional[str] = None) -> Dict[str, Any]:
        """SMS/LMS/MMS 일괄 발송. destinations는 문서 형식 그대로:
        [{"Phone": "010...", "Name": "...", "Company": "...", "Msg_Text": "...",
          "Replace_Datas": [{"Key": "#{성명}", "Value": "홍길동"}, ...]}, ...]
        수신자 수와 관계없이 반드시 이 destinations 배열 하나로 한 번에 호출한다
        (수신자별 반복 호출 금지 - 문서의 호출 제한 규정)."""
        if service not in ("SMS", "LMS", "MMS"):
            return {"Result": "ERROR", "Code": "INVALID_SERVICE",
                    "Message": "service는 SMS/LMS/MMS 중 하나여야 합니다."}
        if not destinations:
            return {"Result": "ERROR", "Code": "NO_DESTINATION", "Message": "수신자가 없습니다."}

        fields = self._auth_fields()
        fields.update({
            "Service": service,
            "Type": "Send",
            "Callback": callback,
            "Main_Text": main_text,
            "Destination": json.dumps(destinations, ensure_ascii=False),
        })
        if subject:
            fields["Subject"] = subject
        if send_date:
            fields["Send_Date"] = send_date  # "YYYY-MM-DD HH:MM"
        return await self._post(fields)

    async def get_reports(self, *, date_start: str, date_end: str,
                           service: str = "SMS", list_ea: int = 100,
                           page: int = 1) -> Dict[str, Any]:
        """발송 결과 전체 목록 조회 (SMS/LMS/MMS 결과가 함께 나온다고 문서에 명시)."""
        fields = self._auth_fields()
        fields.update({
            "Service": service, "Type": "Report",
            "Date_Start": date_start, "Date_End": date_end,
            "List_EA": list_ea, "Page": page,
        })
        return await self._post(fields)

    async def get_report_detail(self, *, job_no: str, service: str = "SMS",
                                 list_ea: int = 200, page: int = 1) -> Dict[str, Any]:
        """특정 발송건(Job_No) 상세 - 수신자별 Phone/Msg_Text/Status/Status_Detail/Done_Date."""
        fields = self._auth_fields()
        fields.update({
            "Service": service, "Type": "Report_Detail",
            "Job_No": job_no, "List_EA": list_ea, "Page": page,
        })
        return await self._post(fields)

    async def get_usage(self, year: int, month: int) -> Dict[str, Any]:
        """월별 사용량조회 (Service=TRAFFIC, Type=List). 응답의 Cash는 발송 응답에서도 얻을 수 있어
        선불잔액 표시에는 마지막 발송 응답의 Cash를 우선 사용한다."""
        fields = self._auth_fields()
        fields.update({
            "Service": "TRAFFIC", "Type": "List",
            "Year": year, "Month": f"{month:02d}",
        })
        return await self._post(fields)


balsong = BalsongClient()
