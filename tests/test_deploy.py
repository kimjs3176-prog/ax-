import importlib
import sys

from fastapi.testclient import TestClient

from agrisea.config import Settings
from agrisea.store import Store
from agrisea.web.app import create_app


def test_admin_disabled_on_serverless_without_token(tmp_path):
    s = Settings(api_key="K", data_dir=tmp_path, admin_token="", serverless=True)
    c = TestClient(create_app(s, Store(":memory:")))
    assert c.post("/api/admin/sample").status_code == 403
    st = c.get("/api/stats").json()
    assert st["admin_required"] and st["serverless"]


def test_admin_token_required_and_checked(tmp_path):
    s = Settings(api_key="K", data_dir=tmp_path, admin_token="secret", serverless=True)
    c = TestClient(create_app(s, Store(":memory:")))
    assert c.post("/api/admin/sample").status_code == 401
    assert c.post("/api/admin/sample", headers={"x-admin-token": "wrong"}).status_code == 401
    r = c.post("/api/admin/sample", headers={"x-admin-token": "secret"})
    assert r.status_code == 200 and r.json()["utterances"] > 0
    # 배치 본문 처리: 대기 건이 없으면 0
    f = c.post("/api/admin/fetch-minutes?limit=2", headers={"x-admin-token": "secret"}).json()
    assert f == {"parsed": 0, "pending": 0, "log": []}


def test_local_admin_open_without_token(tmp_path):
    s = Settings(api_key="K", data_dir=tmp_path, admin_token="", serverless=False)
    c = TestClient(create_app(s, Store(":memory:")))
    assert c.post("/api/admin/sample").status_code == 200


def test_vercel_entrypoint_uses_tmp_and_seed(tmp_path, monkeypatch):
    """VERCEL 환경변수가 있으면 /tmp 계열 경로를 쓰고, 초기 DB가 있으면 복사한다."""
    import agrisea.config as config
    from agrisea import pipeline

    seed = tmp_path / "seed.sqlite3"
    src = Store(seed)
    pipeline.load_sample(src)
    src.conn.close()

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("AGRISEA_DATA_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(config, "SEED_DB", seed)
    monkeypatch.setattr(config, "IS_SERVERLESS", True)
    monkeypatch.setattr(config.Settings, "serverless", True)
    sys.modules.pop("api.index", None)
    mod = importlib.import_module("api.index")
    c = TestClient(mod.app)
    st = c.get("/api/stats").json()
    assert st["storage"] == "seed" and st["meetings"] == 4
    assert (tmp_path / "run" / "agrisea.sqlite3").exists()


def test_health_endpoint(store, settings):
    c = TestClient(create_app(settings, store))
    h = c.get("/api/health").json()
    assert h["ok"] and h["static_index"] and "sqlite" in h and h["fts"] in (True, False)


def test_unwritable_data_dir_falls_back_to_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # 디렉터리 자리에 파일 → mkdir 실패
    s = Settings(api_key="", data_dir=blocker / "sub", serverless=True)
    c = TestClient(create_app(s))
    h = c.get("/api/health").json()
    assert not h["ok"] and h["storage"] == "memory" and h["startup_error"]
    assert c.get("/").status_code == 200
