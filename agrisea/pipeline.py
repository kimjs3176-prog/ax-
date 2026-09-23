"""수집 파이프라인: API 메타데이터 → 회의록 원문(PDF) → 파싱 → 저장 → 지식그래프."""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import requests

from .api_client import AssemblyClient, normalize_row
from .config import ROOT, Settings
from .ontology import build_graph, save_graph
from .parser import extract_pdf_text, parse_minutes
from .store import Store

log = logging.getLogger(__name__)
SAMPLE_DIR = ROOT / "agrisea" / "data" / "sample"


def collect(store: Store, settings: Settings, params: dict[str, Any],
            only_target: bool = True, progress: Callable[[str], None] = print) -> list[dict]:
    client = AssemblyClient(settings)
    meetings = client.committee_meetings(only_target=only_target, **params)
    for m in meetings:
        store.upsert_meeting(m)
    progress(f"회의 {len(meetings)}건 메타데이터 저장 (조건: {params})")
    return meetings


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def download_minutes_text(url: str, dest_dir: Path, name: str,
                          http: requests.Session | None = None) -> str:
    http = http or requests.Session()
    resp = http.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.content
    if data[:4] == b"%PDF":
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w-]+", "_", name)
        (dest_dir / f"{safe}.pdf").write_bytes(data)
        return extract_pdf_text(data)
    text = resp.text
    # 뷰어 페이지 안에 PDF 링크가 있는 경우 한 번 더 따라감
    m = re.search(r"""(https?://[^"'\s>]+\.pdf[^"'\s>]*)""", text, re.I)
    if m and m.group(1) != url:
        return download_minutes_text(m.group(1), dest_dir, name, http)
    return html_to_text(text)


def fetch_minutes(store: Store, settings: Settings, meeting_ids: list[str] | None = None,
                  limit: int | None = None, progress: Callable[[str], None] = print) -> int:
    targets = [m for m in store.meetings()
               if (meeting_ids and m["id"] in meeting_ids)
               or (not meeting_ids and m["text_status"] in ("pending", "failed"))]
    if limit:
        targets = targets[:limit]
    ok = 0
    http = requests.Session()
    for m in targets:
        url = m.get("pdf_url") or m.get("link_url")
        if not url:
            store.set_status(m["id"], "metadata_only")
            continue
        try:
            text = download_minutes_text(url, settings.pdf_dir, m["id"], http)
            doc = parse_minutes(text)
            store.save_minutes(m["id"], doc)
            ok += bool(doc.utterances)
            progress(f"[{m['date']}] {m['title']}: 발언 {len(doc.utterances)}건")
        except Exception as e:  # 한 건 실패가 전체 수집을 멈추지 않도록
            store.set_status(m["id"], "failed")
            progress(f"[실패] {m['id']} {url}: {e}")
    return ok


def ingest_file(store: Store, path: Path, meta: dict[str, Any], is_sample: bool = False) -> int:
    """로컬 PDF/텍스트 회의록을 직접 적재(API 접근이 안 되는 환경용)."""
    data = path.read_bytes()
    text = extract_pdf_text(data) if data[:4] == b"%PDF" else data.decode("utf-8")
    row = normalize_row(meta)
    row["agendas"] = meta.get("agendas", [])
    store.upsert_meeting(row, is_sample=is_sample)
    doc = parse_minutes(text)
    store.save_minutes(row["meeting_id"], doc)
    return len(doc.utterances)


def load_sample(store: Store) -> int:
    index = json.loads((SAMPLE_DIR / "meetings.json").read_text(encoding="utf-8"))
    total = 0
    for item in index:
        total += ingest_file(store, SAMPLE_DIR / item.pop("file"), item, is_sample=True)
    return total


def rebuild_graph(store: Store, settings: Settings):
    g = build_graph(store)
    save_graph(g, settings.graph_path)
    return g
