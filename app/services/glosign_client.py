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
            return {"ok": False, "message": "GLOSIGN_API_KEY 환경변수가 설정되지 않았습니다."}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base_url}/v1/user/company", headers=self._headers)
            if r.status_code == 200:
                return {"ok": True, "message": "글로싸인 연결 성공", "data": r.json()}
            return {"ok": False, "message": f"응답 {r.status_code}", "detail": r.text[:300]}
        except Exception as e:
            return {"ok": False, "message": "연결 실패", "detail": str(e)}

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
