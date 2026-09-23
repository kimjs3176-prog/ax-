"""수집 후 사전 계산: 회의 요약, 쟁점·기관·관계망 집계, 지식그래프(N-Triples).

배포 환경(서버리스)은 요청마다 무거운 계산을 할 수 없어, 자동 수집 단계에서 미리 만들어
초기 데이터(seed)와 data/kg.nt.gz 에 함께 싣는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import analysis
from .ontology import build_graph, to_ntriples_gz
from .store import Store

# 사전 계산 형식이 바뀌면 올린다(초기 데이터를 다시 만들게 함).
PRECOMPUTE_VERSION = 1

CACHED_VIEWS: dict[str, Callable[[Store], object]] = {
    "issues": analysis.issue_overview,
    "orgs": analysis.org_overview,
    "graph": analysis.issue_network,
    "speakers": lambda s: s.speakers(),
}


def precompute(store: Store, kg_path: Path | None = None,
               progress: Callable[[str], None] = print) -> dict:
    n = 0
    for m in store.meetings(status="parsed"):
        if store.summary(m["id"], "rule") is None:
            store.save_summary(m["id"], "rule", analysis.summarize_meeting(store, m["id"]))
            n += 1
    for key, fn in CACHED_VIEWS.items():
        store.kv_set(key, fn(store))
    out = {"summaries": n, "views": list(CACHED_VIEWS)}
    if kg_path:
        g = build_graph(store, text_limit=500)
        to_ntriples_gz(g, kg_path)
        out["triples"] = len(g)
    progress(f"사전 계산: 회의 요약 {n}건, 집계 {len(CACHED_VIEWS)}종"
             + (f", 지식그래프 트리플 {out['triples']}개" if kg_path else ""))
    return out
