"""글로싸인 API 클라이언트 - API Key는 Railway 환경변수에서만 읽음"""
import os, httpx
from typing import Optional

class GlosignClient:
    def __init__(self):
        self.api_key  = os.getenv("GLOSIGN_API_KEY", "")
        self.base_url = os.getenv("GLOSIGN_API_BASE_URL", "https://api.glosign.com").rstrip("/")
        self.webhook_secret = os.getenv("GLOSIGN_WEBHOOK_SECRET", "")

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _ok(self) -> bool:
        return bool(self.api_key)

    async def test_connection(self) -> dict:
        if not self._ok():
            return {"ok": False, "has_api_key": False,
                    "message": "GLOSIGN_API_KEY 환경변수가 설정되지 않았습니다.",
                    "base_url": self.base_url}
        results = []
        for endpoint in ["/v1/user", "/v1/user/company"]:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(f"{self.base_url}{endpoint}", headers=self._headers)
                res = {
                    "endpoint": endpoint, "status_code": r.status_code,
                    "response_body": r.text[:200],
                }
                if r.status_code == 200:
                    res.update({"ok": True, "message": "연결 성공"})
                    results.append(res)
                    break
                elif r.status_code == 403:
                    res.update({"ok": False,
                        "message": "글로싸인 서버에는 연결되었지만 API 권한이 거절되었습니다. "
                                   "API Key 활성화 여부, 테스트모드, 이용 플랜, endpoint 권한을 확인해주세요. (403)"})
                elif r.status_code == 401:
                    res.update({"ok": False, "message": "API Key 인증 실패 (401). Key 값을 확인해주세요."})
                else:
                    res.update({"ok": False, "message": f"예상치 못한 응답 ({r.status_code})"})
                results.append(res)
            except Exception as e:
                results.append({"endpoint": endpoint, "ok": False,
                                 "message": "연결 실패", "detail": str(e)})
        best = next((x for x in results if x.get("ok")), results[0] if results else {})
        return {
            "ok": best.get("ok", False),
            "has_api_key": True,
            "masked_api_key": self.api_key[:4] + "****" + self.api_key[-4:] if len(self.api_key) > 8 else "****",
            "base_url": self.base_url,
            "message": best.get("message", ""),
            "endpoints_tested": results,
        }

    async def get_document_status(self, document_id: str) -> dict:
        if not self._ok(): return {"ok": False, "message": "API Key 없음"}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base_url}/v1/documents/{document_id}", headers=self._headers)
            if r.status_code == 200:
                return {"ok": True, "data": r.json()}
            return {"ok": False, "status_code": r.status_code, "detail": r.text[:300]}
        except Exception as e:
            return {"ok": False, "detail": str(e)}

glosign = GlosignClient()
