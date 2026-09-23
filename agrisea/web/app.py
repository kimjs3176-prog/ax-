"""FastAPI 웹 서비스: 검색·요약·브리핑·온톨로지 탐색 API와 단일 페이지 UI."""
from __future__ import annotations

import hmac
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from rdflib import Graph

from .. import analysis
from ..config import Settings, get_settings
from ..lexicon import ISSUES, ORGANIZATIONS
from ..ontology import PRESET_QUERIES, build_graph, run_sparql, save_graph
from ..store import Store

STATIC = Path(__file__).parent / "static"
FORBIDDEN_SPARQL = re.compile(r"\b(SERVICE|LOAD|INSERT|DELETE|DROP|CLEAR|CREATE|COPY|MOVE|ADD)\b", re.I)


class GraphCache:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self._g: Graph | None = None
        self._lock = threading.Lock()

    def get(self) -> Graph:
        with self._lock:
            if self._g is None:
                self._g = build_graph(self.store)
            return self._g

    def invalidate(self) -> Graph:
        with self._lock:
            self._g = build_graph(self.store)
            save_graph(self._g, self.settings.graph_path)
            return self._g


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    storage = "memory"
    if store is None:
        from ..pipeline import seed_store
        storage = "seed" if seed_store(settings) else "local"
        settings.ensure_dirs()
        store = Store(settings.db_path)
    if settings.serverless and storage != "memory":
        storage = "seed" if storage == "seed" else "ephemeral"

    def require_admin(x_admin_token: str = Header(default="")) -> None:
        """관리 기능 보호: 토큰이 설정돼 있으면 일치해야 하고, 서버리스에선 토큰이 필수."""
        if settings.admin_token:
            if not hmac.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
                raise HTTPException(401, "관리자 토큰이 올바르지 않습니다.")
        elif settings.serverless:
            raise HTTPException(403, "배포 환경에서는 ADMIN_TOKEN 환경변수를 설정해야 관리 기능을 쓸 수 있습니다.")

    graphs = GraphCache(store, settings)
    app = FastAPI(title="농해수위 국정감사 온톨로지 서비스", version="0.1.0")
    app.state.store = store

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/stats")
    def stats():
        return {**store.stats(), "llm_enabled": settings.llm_enabled,
                "api_key_configured": bool(settings.api_key),
                "storage": storage, "serverless": settings.serverless,
                "admin_required": bool(settings.admin_token) or settings.serverless}

    @app.get("/api/meetings")
    def meetings(date_from: str = "", date_to: str = "", q: str = ""):
        return store.meetings(date_from, date_to, q)

    @app.get("/api/meetings/{meeting_id}")
    def meeting(meeting_id: str):
        m = store.meeting(meeting_id)
        if not m:
            raise HTTPException(404, "회의를 찾을 수 없습니다.")
        return {"meeting": m, "utterances": store.utterances(meeting_id=meeting_id)}

    @app.get("/api/meetings/{meeting_id}/summary")
    def summary(meeting_id: str, llm: bool = False):
        if not store.meeting(meeting_id):
            raise HTTPException(404, "회의를 찾을 수 없습니다.")
        base = analysis.summarize_meeting(store, meeting_id)
        cached = store.summary(meeting_id, "llm")
        if llm and not cached:
            if not settings.llm_enabled:
                raise HTTPException(400, "ANTHROPIC_API_KEY가 설정되지 않아 LLM 요약을 사용할 수 없습니다.")
            from ..llm import llm_summarize
            try:
                cached = llm_summarize(store, meeting_id, settings)
            except Exception as e:
                raise HTTPException(502, f"LLM 요약 실패: {e}") from e
        base["llm"] = cached
        return base

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1), speaker_type: str = "", date_from: str = "",
               date_to: str = "", limit: int = Query(50, le=500)):
        rows = store.search(q, limit, speaker_type, date_from, date_to)
        return {"query": q, "count": len(rows), "results": rows,
                "issues": analysis._issue_rank(rows, 6)}

    @app.get("/api/issues")
    def issues():
        return analysis.issue_overview(store)

    @app.get("/api/orgs")
    def orgs():
        cnt, commits = Counter(), Counter()
        for u in store.utterances():
            for o in set(u["orgs"]) | ({u["org"]} if u["org"] else set()):
                cnt[o] += 1
            if u["is_commitment"] and u["org"]:
                commits[u["org"]] += 1
        return [{"name": n, "aliases": list(ORGANIZATIONS[n]), "mentions": cnt[n],
                 "commitments": commits[n]} for n in ORGANIZATIONS]

    @app.get("/api/speakers")
    def speakers():
        return store.speakers()

    @app.get("/api/briefing")
    def briefing(org: str = "", issue: str = "", keyword: str = "", member: str = "",
                 date_from: str = "", date_to: str = "", format: str = "json"):
        b = analysis.briefing(store, org, issue, keyword, member, date_from, date_to)
        if format == "md":
            return PlainTextResponse(analysis.briefing_markdown(b),
                                     media_type="text/markdown; charset=utf-8")
        return b

    @app.get("/api/taxonomy")
    def taxonomy():
        return [{"id": k, "label": v[0], "parent": v[1], "keywords": list(v[2])}
                for k, v in ISSUES.items()]

    @app.get("/api/sparql/presets")
    def presets():
        return PRESET_QUERIES

    @app.post("/api/sparql")
    def sparql(payload: dict[str, Any] = Body(...)):
        query = str(payload.get("query", ""))
        if FORBIDDEN_SPARQL.search(query):
            raise HTTPException(400, "읽기 전용 SELECT/ASK/CONSTRUCT 질의만 허용됩니다.")
        try:
            return run_sparql(graphs.get(), query)
        except Exception as e:
            raise HTTPException(400, f"SPARQL 오류: {e}") from e

    @app.get("/api/graph")
    def graph(min_weight: int = 1):
        """쟁점–기관–위원 관계망(시각화용)."""
        nodes: dict[str, dict] = {}
        edges: Counter = Counter()

        def node(nid: str, label: str, kind: str):
            n = nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, "weight": 0})
            n["weight"] += 1

        for u in store.utterances():
            issues_ = [i for i in u["issues"]]
            orgs_ = set(u["orgs"]) | ({u["org"]} if u["org"] else set())
            for i in issues_:
                node(f"issue:{i}", ISSUES[i][0], "issue")
                for o in orgs_:
                    node(f"org:{o}", o, "org")
                    edges[(f"issue:{i}", f"org:{o}")] += 1
                if u["speaker_type"] == "member":
                    node(f"member:{u['speaker_name']}", f"{u['speaker_name']} 위원", "member")
                    edges[(f"member:{u['speaker_name']}", f"issue:{i}")] += 1
        return {"nodes": list(nodes.values()),
                "edges": [{"source": a, "target": b, "weight": w}
                          for (a, b), w in edges.items() if w >= min_weight]}

    @app.get("/api/ontology.ttl")
    def ontology_ttl():
        return PlainTextResponse(graphs.get().serialize(format="turtle"),
                                 media_type="text/turtle; charset=utf-8")

    @app.post("/api/admin/collect", dependencies=[Depends(require_admin)])
    def admin_collect(payload: dict[str, Any] = Body(...)):
        """회의 메타데이터 수집. date_from/date_to를 주면 일자별로 순회한다(본문은 fetch-minutes로)."""
        from ..pipeline import collect, collect_range, pending_count
        if not settings.api_key:
            raise HTTPException(400, "ASSEMBLY_API_KEY가 설정되지 않았습니다.")
        extra = {str(k): str(v) for k, v in (payload.get("params") or {}).items()}
        base = {"DAE_NUM": str(payload.get("dae") or "22"), **extra}
        log: list[str] = []
        if settings.serverless and payload.get("date_from"):
            from datetime import date
            try:
                span = (date.fromisoformat(payload.get("date_to") or payload["date_from"])
                        - date.fromisoformat(payload["date_from"])).days
            except ValueError as e:
                raise HTTPException(400, f"날짜 형식 오류: {e}") from e
            if span > 31:
                raise HTTPException(400, "배포 환경에서는 실행시간 제한 때문에 한 번에 31일까지만 수집할 수 있습니다. "
                                         "긴 기간은 GitHub Actions 「회의록 데이터 갱신」을 사용하세요.")
        try:
            if payload.get("date_from"):
                ms = collect_range(store, settings, payload["date_from"],
                                   payload.get("date_to") or payload["date_from"],
                                   base, progress=log.append)
            else:
                ms = collect(store, settings, {**base, "CONF_DATE": payload.get("date")},
                             progress=log.append)
        except Exception as e:
            raise HTTPException(502, f"수집 실패: {e}") from e
        graphs.invalidate()
        return {"meetings": len(ms), "pending": pending_count(store), "log": log}

    @app.post("/api/admin/fetch-minutes", dependencies=[Depends(require_admin)])
    def admin_fetch_minutes(limit: int = Query(3, ge=1, le=20)):
        """본문 미수집 회의를 limit건씩 처리(서버리스 실행시간 제한 대응). pending이 0이 될 때까지 반복 호출."""
        from ..pipeline import fetch_minutes, pending_count
        log: list[str] = []
        ok = fetch_minutes(store, settings, limit=limit, progress=log.append)
        graphs.invalidate()
        return {"parsed": ok, "pending": pending_count(store), "log": log}

    @app.post("/api/admin/sample", dependencies=[Depends(require_admin)])
    def admin_sample():
        from ..pipeline import load_sample
        n = load_sample(store)
        graphs.invalidate()
        return {"utterances": n}

    return app
