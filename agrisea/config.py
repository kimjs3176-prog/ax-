"""환경 설정.

인증키는 코드에 넣지 않고 환경변수(또는 프로젝트 루트의 .env)에서 읽는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

API_URL = "https://open.assembly.go.kr/portal/openapi/ncwgseseafwbuheph"
COMMITTEE_NAME = "농림축산식품해양수산위원회"
# 대수별 위원회 명칭 변화(19대 이전: 농림수산식품위원회 등)를 함께 매칭
COMMITTEE_ALIASES = (
    "농림축산식품해양수산위원회",
    "농해수위",
    "농림해양수산위원회",
    "농림수산식품위원회",
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.environ.get("ASSEMBLY_API_KEY", ""))
    api_url: str = field(default_factory=lambda: os.environ.get("ASSEMBLY_API_URL", API_URL))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AGRISEA_DATA_DIR", ROOT / "var"))
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get("AGRISEA_LLM_MODEL", "claude-opus-5")
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agrisea.sqlite3"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdf"

    @property
    def graph_path(self) -> Path:
        return self.data_dir / "agrisea_kg.ttl"

    @property
    def llm_enabled(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
