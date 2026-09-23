"""Vercel 서버리스 진입점: 모든 요청을 FastAPI 앱으로 전달한다(vercel.json rewrites).

앱 생성이 실패해도 함수 자체는 뜨도록, 실패 원인을 보여 주는 최소 앱으로 대체한다.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from agrisea.web.app import create_app

    app = create_app()
except Exception:  # noqa: BLE001 - 배포 진단용
    _error = traceback.format_exc()
    print(_error, file=sys.stderr)

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def startup_failed(path: str):
        return JSONResponse({"error": "앱 초기화 실패", "traceback": _error}, status_code=500)
