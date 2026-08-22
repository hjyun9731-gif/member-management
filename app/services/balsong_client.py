"""발송닷컴(balsong.com) SMS/LMS/MMS API 클라이언트.

계정 ID/비밀번호는 Railway 환경변수(BALSONG_USER_ID / BALSONG_USER_PW)에서만
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

로깅 원칙:
- UserID/UserPW 값 자체는 어떤 경우에도 로그에 남기지 않는다.
- 매 호출마다 (1) 요청 실행 여부 (2) HTTP status code (3) 응답 content-type
  (4) 비밀정보를 제외한 응답 내용(길이 제한) (5) 성공/실패 판정만 남긴다.
"""
import os
import json
import ssl
import time
import asyncio
import logging
import re
import httpx
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

BASE_URL = "https://balsong.com/Linkage/API/"

# 무한 대기 방지용 명시적 타임아웃 (초 단위).
# connect: TCP 연결 자체가 안 되는 경우, read: 요청은 보냈으나 응답을 못 받는 경우,
# write: 요청 본문 전송이 막히는 경우, pool: 커넥션 풀에서 연결을 못 받는 경우.
_HTTPX_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)

# 문서상 제한: 1초 3회 이상 → 10분 차단. 여유를 두고 최소 호출 간격을 둔다.
_MIN_CALL_INTERVAL_SEC = 0.5
_last_call_lock = asyncio.Lock()
_last_call_ts = 0.0

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_markup(text: str, limit: int = 200) -> str:
    """응답이 HTML/SVG 등 마크업일 경우 태그를 제거하고 길이를 제한한다.
    사용자에게 그대로 노출돼도 안전한 문자열만 남긴다."""
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


class BalsongClient:
    def __init__(self):
        self.username = os.getenv("BALSONG_USER_ID", "")
        self.password = os.getenv("BALSONG_USER_PW", "")
        # 값 자체는 절대 로그에 남기지 않고, 인식 여부(true/false)만 기록한다.
        # Railway에서 변수가 실제로 컨테이너에 주입됐는지 배포 후 바로 확인하기 위한 용도.
        logger.info("BALSONG_USER_ID configured: %s", bool(self.username))
        logger.info("BALSONG_USER_PW configured: %s", bool(self.password))

    def _missing_vars(self) -> List[str]:
        missing = []
        if not self.username:
            missing.append("BALSONG_USER_ID")
        if not self.password:
            missing.append("BALSONG_USER_PW")
        return missing

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
        service = fields.get("Service", "?")
        req_type = fields.get("Type", "?")

        if not self._ok():
            missing = self._missing_vars()
            logger.warning(
                "발송닷컴 요청 미실행 (Service=%s Type=%s): 환경변수 누락 %s",
                service, req_type, missing,
            )
            return {"Result": "ERROR", "Code": "NO_CREDENTIALS",
                    "Message": f"Railway 환경변수가 설정되지 않았습니다: {', '.join(missing)}"}

        await self._throttle()

        multipart_fields = []
        for key, value in fields.items():
            if value is None:
                continue
            multipart_fields.append(
                (key, (None, str(value).encode("utf-8"), "text/plain; charset=utf-8"))
            )

        dest_count = None
        if "Destination" in fields and fields["Destination"] is not None:
            try:
                dest_count = len(json.loads(fields["Destination"]))
            except Exception:
                dest_count = -1  # 파싱 실패 시 -1로 표시 (값 자체는 로그에 남기지 않음)
        msg_len = len(str(fields.get("Main_Text") or ""))
        field_keys = list(fields.keys())  # 값이 아니라 필드 이름만 (UserID/UserPW 등 값은 절대 포함 안 함)

        logger.info(
            "발송닷컴 요청 시작: method=POST url=%s Service=%s Type=%s "
            "fields=%s destination_count=%s message_len=%s timeout(connect/read/write/pool)=%s/%s/%s/%s",
            BASE_URL, service, req_type, field_keys, dest_count, msg_len,
            _HTTPX_TIMEOUT.connect, _HTTPX_TIMEOUT.read, _HTTPX_TIMEOUT.write, _HTTPX_TIMEOUT.pool,
        )
        started = time.monotonic()

        def _elapsed() -> float:
            return round(time.monotonic() - started, 3)

        try:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as c:
                # 실제로 전송될 요청을 먼저 만들어서, httpx가 채워넣은 진짜
                # Content-Type(=multipart/form-data; boundary=...)을 전송 직전에 확인/기록한다.
                # (요청 자체는 이 request 객체를 그대로 보내므로 이중 인코딩되지 않는다.)
                req = c.build_request("POST", BASE_URL, files=multipart_fields)
                sent_content_type = req.headers.get("content-type", "")
                logger.info(
                    "발송닷컴 요청 전송 직전 Content-Type: %s (Service=%s Type=%s)",
                    sent_content_type, service, req_type,
                )
                r = await c.send(req)
            elapsed = _elapsed()
            content_type = r.headers.get("content-type", "")
            try:
                body = r.json()
                logger.info(
                    "발송닷컴 응답 수신: status=%s content-type=%s elapsed=%.3fs Result=%s Code=%s",
                    r.status_code, content_type, elapsed, body.get("Result"), body.get("Code"),
                )
                return body
            except Exception:
                safe_text = _strip_markup(r.text)
                logger.warning(
                    "발송닷컴 응답 수신(JSON 아님): status=%s content-type=%s elapsed=%.3fs body(요약)=%r",
                    r.status_code, content_type, elapsed, safe_text,
                )
                return {"Result": "ERROR", "Code": f"HTTP_{r.status_code}",
                        "Message": safe_text or f"발송닷컴 서버가 예상치 못한 응답(HTTP {r.status_code})을 반환했습니다."}

        except httpx.ConnectTimeout as e:
            elapsed = _elapsed()
            logger.warning(
                "발송닷컴 요청 실패 [ConnectTimeout] Service=%s Type=%s elapsed=%.3fs: %s",
                service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "CONNECT_TIMEOUT",
                    "Message": "발송닷컴 연결 시간 초과"}

        except httpx.ReadTimeout as e:
            elapsed = _elapsed()
            logger.warning(
                "발송닷컴 요청 실패 [ReadTimeout] Service=%s Type=%s elapsed=%.3fs: %s "
                "(TCP 연결은 성공, 응답 대기 중 초과)",
                service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "READ_TIMEOUT",
                    "Message": "발송닷컴 응답 시간 초과"}

        except httpx.RemoteProtocolError as e:
            elapsed = _elapsed()
            logger.warning(
                "발송닷컴 요청 실패 [RemoteProtocolError] Service=%s Type=%s elapsed=%.3fs: %s",
                service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "PROTOCOL_ERROR",
                    "Message": "발송닷컴 서버와의 통신 프로토콜 오류"}

        except httpx.ConnectError as e:
            elapsed = _elapsed()
            cause = e.__cause__
            is_ssl = isinstance(cause, ssl.SSLError) or "ssl" in cause.__class__.__name__.lower() if cause else False
            if is_ssl:
                logger.warning(
                    "발송닷컴 요청 실패 [ConnectError/SSL] Service=%s Type=%s elapsed=%.3fs: %s (원인=%s)",
                    service, req_type, elapsed, e.__class__.__name__,
                    cause.__class__.__name__ if cause else "unknown",
                )
                return {"Result": "ERROR", "Code": "SSL_ERROR",
                        "Message": "발송닷컴 HTTPS 연결 실패 (SSL/TLS 오류)"}
            logger.warning(
                "발송닷컴 요청 실패 [ConnectError] Service=%s Type=%s elapsed=%.3fs: %s (DNS/TCP 연결 단계)",
                service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "CONNECT_ERROR",
                    "Message": "발송닷컴 HTTPS 연결 실패"}

        except httpx.HTTPError as e:
            elapsed = _elapsed()
            logger.warning(
                "발송닷컴 요청 실패 [HTTPError:%s] Service=%s Type=%s elapsed=%.3fs: %s",
                e.__class__.__name__, service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "HTTP_ERROR",
                    "Message": "발송닷컴 요청 처리 중 오류가 발생했습니다"}

        except Exception as e:
            elapsed = _elapsed()
            logger.warning(
                "발송닷컴 요청 실패 [%s] Service=%s Type=%s elapsed=%.3fs: %s",
                e.__class__.__name__, service, req_type, elapsed, e.__class__.__name__,
            )
            return {"Result": "ERROR", "Code": "EXCEPTION",
                    "Message": "발송닷컴 서버에 연결할 수 없습니다."}

    async def test_connection(self) -> Dict[str, Any]:
        """계정 정보 유무만 확인. 부작용 없는 사용량조회(TRAFFIC/List) 호출로 실제 인증까지 검증."""
        if not self._ok():
            missing = self._missing_vars()
            return {"ok": False, "has_credentials": False,
                    "message": f"Railway 환경변수가 설정되지 않았습니다: {', '.join(missing)}"}
        from datetime import date
        today = date.today()
        raw = await self.get_usage(today.year, today.month)
        ok = raw.get("Result") == "OK"
        return {"ok": ok, "has_credentials": True, "raw": raw}

    async def network_diagnostics(self) -> Dict[str, Any]:
        """balsong.com 서버 자체에 대한 저수준 네트워크 점검 (인증정보/발송 없이).
        운영(Railway) 컨테이너에서 실행해야 실제 원인 파악에 의미가 있다 —
        이 서버가 있는 네트워크에서 balsong.com이 IP 차단/화이트리스트/포트 문제로
        막혀 있는지, 아니면 TCP/TLS는 되는데 우리 요청 방식(HTTP)에서 응답이 없는지를
        구분하기 위한 것. UserID/UserPW/실제 발송 데이터는 전혀 사용하지 않는다."""
        import socket
        host = "balsong.com"
        result: Dict[str, Any] = {"host": host}

        # 1) DNS 해석
        t0 = time.monotonic()
        try:
            addrs = socket.getaddrinfo(host, 443)
            result["dns"] = {"ok": True, "elapsed": round(time.monotonic() - t0, 3),
                              "addresses": sorted({a[4][0] for a in addrs})}
        except Exception as e:
            result["dns"] = {"ok": False, "elapsed": round(time.monotonic() - t0, 3),
                              "error": e.__class__.__name__}
            return result  # DNS부터 안 되면 이후 단계는 의미 없음

        # 2) TCP 443 포트 연결 (순수 소켓, TLS 이전)
        t0 = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 443), timeout=10.0)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            result["tcp_connect"] = {"ok": True, "elapsed": round(time.monotonic() - t0, 3)}
        except Exception as e:
            result["tcp_connect"] = {"ok": False, "elapsed": round(time.monotonic() - t0, 3),
                                      "error": e.__class__.__name__}
            return result

        # 3) TLS 핸드셰이크
        t0 = time.monotonic()
        try:
            ctx = ssl.create_default_context()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host), timeout=10.0)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            result["tls_handshake"] = {"ok": True, "elapsed": round(time.monotonic() - t0, 3)}
        except Exception as e:
            result["tls_handshake"] = {"ok": False, "elapsed": round(time.monotonic() - t0, 3),
                                        "error": e.__class__.__name__}
            return result

        # 4) 인증정보 없이 순수 GET (엔드포인트가 존재하는지, 어떤 응답을 주는지 확인용.
        #    실패해도 정상 - 이 API가 GET을 지원 안 할 수도 있음. 상태코드/응답만 참고.)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
                r = await c.get(BASE_URL)
            result["plain_get"] = {
                "ok": True, "elapsed": round(time.monotonic() - t0, 3),
                "status": r.status_code, "content_type": r.headers.get("content-type", ""),
                "body_preview": _strip_markup(r.text, 150),
            }
        except Exception as e:
            result["plain_get"] = {"ok": False, "elapsed": round(time.monotonic() - t0, 3),
                                    "error": e.__class__.__name__}

        return result

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

        # 문서 기준 필수 필드: UserID, UserPW, Service, Type, Callback, Subject,
        # Main_Text, Destination. Callback은 하이픈 없는 숫자만, Subject는 값이
        # 없더라도 필드 자체는 항상 보낸다(문서상 필수 필드이므로 생략하지 않는다).
        fields = self._auth_fields()
        fields.update({
            "Service": service,
            "Type": "Send",
            "Callback": re.sub(r"\D", "", callback or ""),
            "Subject": subject or "",
            "Main_Text": main_text,
            "Destination": json.dumps(destinations, ensure_ascii=False),
        })
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
