"""Vercel 서버리스 진입점: 모든 요청을 FastAPI 앱으로 전달한다(vercel.json rewrites)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agrisea.web.app import create_app  # noqa: E402

app = create_app()
