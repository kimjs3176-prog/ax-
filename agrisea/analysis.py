"""회의 요약, 핵심안건 정리, 국정감사 대비 브리핑 생성(규칙 기반)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .lexicon import ISSUES, ORGANIZATIONS, commitment_sentences, issue_label
from .nlp import extractive_summary, keywords, truncate
from .store import Store


def _issue_rank(utts: list[dict], top: int = 8) -> list[dict]:
    cnt: Counter = Counter()
    for u in utts:
        cnt.update(u["issues"])
    out = []
    for iid, n in cnt.most_common():
        parent = ISSUES[iid][1]
        out.append({"id": iid, "label": issue_label(iid), "count": n,
                    "parent": issue_label(parent) if parent else None})
    return out[:top]


def _names(utts: list[dict]) -> set[str]:
    return {u["speaker_name"] for u in utts} | {u["speaker_role"] for u in utts}


def qa_pairs(utts: list[dict]) -> list[dict]:
    """위원 질의 → 이어지는 정부측 답변 묶음."""
    pairs: list[dict] = []
    cur: dict | None = None
    for u in utts:
        if u["speaker_type"] == "member" and u["is_question"]:
            cur = {"question": u, "answers": []}
            pairs.append(cur)
        elif u["speaker_type"] in ("official", "witness") and cur is not None:
            cur["answers"].append(u)
        elif u["speaker_type"] == "chair":
            cur = None  # 위원장 발언 → 질의 순서 전환
    return pairs


def _pair_view(p: dict) -> dict:
    q = p["question"]
    ans = p["answers"]
    return {
        "meeting_id": q["meeting_id"],
        "date": q.get("meeting_date"),
        "member": q["speaker_name"],
        "question": " ".join(extractive_summary(q["text"], 2)) or truncate(q["text"], 200),
        "question_full": q["text"],
        "answerer": f"{ans[0]['speaker_role']} {ans[0]['speaker_name']}" if ans else None,
        "answer": " ".join(extractive_summary(" ".join(a["text"] for a in ans), 2)) if ans else None,
        "commitment": any(a["is_commitment"] for a in ans),
        "issues": [issue_label(i) for i in q["issues"]],
    }


def _pair_score(p: dict) -> float:
    q = p["question"]
    return (len(q["issues"]) * 2 + len(q["orgs"]) + min(len(q["text"]) / 300, 3)
            + (2 if any(a["is_commitment"] for a in p["answers"]) else 0)
            + (1 if q["is_data_request"] else 0))


def commitment_view(u: dict) -> dict:
    return {
        "meeting_id": u["meeting_id"], "date": u.get("meeting_date"),
        "speaker": f"{u['speaker_role']} {u['speaker_name']}", "org": u["org"],
        "text": truncate(" ".join(commitment_sentences(u["text"])[:2]) or u["text"], 260),
        "issues": [issue_label(i) for i in u["issues"]],
    }


def summarize_meeting(store: Store, meeting_id: str) -> dict[str, Any]:
    m = store.meeting(meeting_id)
    if not m:
        raise KeyError(meeting_id)
    utts = store.utterances(meeting_id=meeting_id)
    members = Counter(u["speaker_name"] for u in utts if u["speaker_type"] == "member")
    officials = Counter(f"{u['speaker_role']} {u['speaker_name']}" for u in utts
                        if u["speaker_type"] in ("official", "witness"))
    n_q = sum(u["is_question"] for u in utts)
    n_c = sum(u["is_commitment"] for u in utts)
    substantive = [u for u in utts if u["speaker_type"] != "chair"]
    full_text = " ".join(u["text"] for u in substantive)

    agenda_summaries = []
    for i, a in enumerate(m["agendas"]):
        a_utts = [u for u in substantive if u["agenda_idx"] == i]
        if not a_utts:
            agenda_summaries.append({"agenda": a, "summary": [], "speakers": [], "issues": []})
            continue
        agenda_summaries.append({
            "agenda": a,
            "summary": extractive_summary(" ".join(u["text"] for u in a_utts), 3),
            "speakers": [n for n, _ in Counter(u["speaker_name"] for u in a_utts).most_common(6)],
            "issues": [x["label"] for x in _issue_rank(a_utts, 4)],
        })

    pairs = sorted(qa_pairs(utts), key=_pair_score, reverse=True)
    overview = (f"{m['date']} {m['title']} — 안건 {len(m['agendas'])}건, 발언 {len(utts)}회"
                f"(위원 질의 {n_q}회, 이행약속 답변 {n_c}회), 질의 위원 {len(members)}명.")
    if not utts:
        overview = (f"{m['date']} {m['title']} — 회의록 본문이 아직 수집되지 않았습니다. "
                    f"안건 {len(m['agendas'])}건.")
    return {
        "meeting": m,
        "method": "rule-based",
        "overview": overview,
        "agendas": m["agendas"],
        "key_issues": _issue_rank(substantive),
        "keywords": [w for w, _ in keywords([full_text], 15, exclude=_names(utts))]
                    if full_text else [],
        "key_points": extractive_summary(full_text, 5) if full_text else [],
        "agenda_summaries": agenda_summaries,
        "qa_highlights": [_pair_view(p) for p in pairs[:8]],
        "commitments": [commitment_view(u) for u in utts if u["is_commitment"]],
        "data_requests": [{"member": u["speaker_name"], "text": truncate(u["text"], 200)}
                          for u in utts if u["is_data_request"]],
        "participants": {
            "members": [{"name": n, "count": c} for n, c in members.most_common()],
            "officials": [{"name": n, "count": c} for n, c in officials.most_common()],
        },
    }


def briefing(store: Store, org: str = "", issue: str = "", keyword: str = "",
             member: str = "", date_from: str = "", date_to: str = "",
             top: int = 10, session: int | None = None) -> dict[str, Any]:
    """국정감사 대비 브리핑: 대상(기관/쟁점/키워드/위원) 관련 과거 회의록을 모아 정리."""
    utts = store.utterances(session=session)
    issue_ids = set()
    if issue:
        issue_ids = {iid for iid, (label, parent, _) in ISSUES.items()
                     if iid == issue or label == issue or parent == issue
                     or (parent and ISSUES[parent][0] == issue)}

    def keep(u: dict) -> bool:
        if date_from and (u["meeting_date"] or "") < date_from:
            return False
        if date_to and (u["meeting_date"] or "") > date_to:
            return False
        if org and not (u["org"] == org or org in u["orgs"]):
            return False
        if issue_ids and not (issue_ids & set(u["issues"])):
            return False
        if keyword and keyword not in u["text"]:
            return False
        if member and u["speaker_name"] != member:
            return False
        return True

    # 질의가 조건에 맞으면 그 답변도 포함(답변에 기관명이 없어도 맥락 유지)
    by_meeting: dict[str, list[dict]] = defaultdict(list)
    for u in utts:
        by_meeting[u["meeting_id"]].append(u)
    selected_pairs, selected = [], []
    for mid, mu in by_meeting.items():
        for p in qa_pairs(mu):
            if keep(p["question"]) or (not member and any(keep(a) for a in p["answers"])):
                selected_pairs.append(p)
        selected.extend(u for u in mu if u["speaker_type"] != "chair" and keep(u))

    commitments = [u for u in utts if u["is_commitment"] and keep(u)]
    for p in selected_pairs:
        for a in p["answers"]:
            if a["is_commitment"] and a not in commitments:
                commitments.append(a)

    timeline = Counter(u["meeting_date"] for u in selected)
    meetings_idx = {m["id"]: m for m in store.meetings(session=session)}
    meeting_list = sorted({u["meeting_id"] for u in selected},
                          key=lambda mid: meetings_idx[mid]["date"] or "", reverse=True)
    members = Counter(p["question"]["speaker_name"] for p in selected_pairs)
    pairs_sorted = sorted(selected_pairs, key=_pair_score, reverse=True)
    all_text = " ".join(u["text"] for u in selected)

    target = " / ".join(x for x in (org, issue and next(
        (ISSUES[i][0] for i in ISSUES if i == issue), issue), keyword, member) if x) or "전체"
    return {
        "target": target,
        "filters": {"org": org, "issue": issue, "keyword": keyword, "member": member,
                    "date_from": date_from, "date_to": date_to, "session": session},
        "counts": {"utterances": len(selected), "meetings": len(meeting_list),
                   "qa_pairs": len(selected_pairs), "commitments": len(commitments)},
        "key_issues": _issue_rank(selected),
        "keywords": [w for w, _ in keywords([all_text], 20, exclude=_names(utts))]
                    if all_text else [],
        "key_points": extractive_summary(all_text, 6) if all_text else [],
        "top_questions": [_pair_view(p) for p in pairs_sorted[:top]],
        "commitments": [commitment_view(u) for u in
                        sorted(commitments, key=lambda u: u["meeting_date"] or "", reverse=True)],
        "active_members": [{"name": n, "count": c} for n, c in members.most_common(10)],
        "timeline": [{"date": d, "count": c} for d, c in sorted(timeline.items())],
        "meetings": [{"id": mid, "date": meetings_idx[mid]["date"],
                      "title": meetings_idx[mid]["title"]} for mid in meeting_list],
    }


def briefing_markdown(b: dict) -> str:
    L = [f"# 국정감사 대비 브리핑: {b['target']}", ""]
    c = b["counts"]
    L.append(f"- 분석 범위: 회의 {c['meetings']}건, 발언 {c['utterances']}회, "
             f"질의·답변 {c['qa_pairs']}쌍, 이행약속 답변 {c['commitments']}건")
    if b["filters"].get("session"):
        L.append(f"- 회기: 제{b['filters']['session']}회")
    if b["filters"]["date_from"] or b["filters"]["date_to"]:
        L.append(f"- 기간: {b['filters']['date_from'] or '처음'} ~ {b['filters']['date_to'] or '현재'}")
    L += ["", "## 1. 핵심 쟁점"]
    for i in b["key_issues"]:
        L.append(f"- **{i['label']}** ({i['count']}회)" + (f" · 상위: {i['parent']}" if i["parent"] else ""))
    if b["keywords"]:
        L += ["", "주요 키워드: " + ", ".join(b["keywords"])]
    L += ["", "## 2. 주요 내용"]
    L += [f"- {s}" for s in b["key_points"]] or ["- (해당 발언 없음)"]
    L += ["", "## 3. 주요 질의와 정부 답변"]
    for n, q in enumerate(b["top_questions"], 1):
        L.append(f"{n}. **{q['member']} 위원** ({q['date']}) — {q['question']}")
        if q["answer"]:
            L.append(f"   - 답변({q['answerer']}): {q['answer']}" + (" **[이행약속]**" if q["commitment"] else ""))
    L += ["", "## 4. 이행약속 추적표 (사후 점검 대상)", "",
          "| 일자 | 답변자 | 기관 | 내용 |", "|---|---|---|---|"]
    for cm in b["commitments"]:
        L.append(f"| {cm['date']} | {cm['speaker']} | {cm['org'] or '-'} | {cm['text'].replace('|', '/')} |")
    L += ["", "## 5. 관심 위원"]
    L += [f"- {m['name']} 위원: 질의 {m['count']}회" for m in b["active_members"]]
    L += ["", "## 6. 관련 회의"]
    L += [f"- {m['date']} {m['title']}" for m in b["meetings"]]
    return "\n".join(L) + "\n"


def issue_overview(store: Store) -> list[dict]:
    utts = store.utterances()
    per_issue: dict[str, dict] = {}
    for iid, (label, parent, _) in ISSUES.items():
        per_issue[iid] = {"id": iid, "label": label, "parent": parent, "count": 0,
                          "meetings": set(), "orgs": Counter(), "members": Counter()}
    for u in utts:
        for iid, n in u["issues"].items():
            d = per_issue[iid]
            d["count"] += n
            d["meetings"].add(u["meeting_id"])
            d["orgs"].update(u["orgs"])
            if u["org"]:
                d["orgs"][u["org"]] += 1
            if u["speaker_type"] == "member":
                d["members"][u["speaker_name"]] += 1
            parent = ISSUES[iid][1]
            if parent:
                per_issue[parent]["count"] += n
                per_issue[parent]["meetings"].add(u["meeting_id"])
    out = []
    for d in per_issue.values():
        out.append({**d, "meetings": len(d["meetings"]),
                    "orgs": [o for o, _ in d["orgs"].most_common(5)],
                    "members": [m for m, _ in d["members"].most_common(5)]})
    return out


def org_overview(store: Store) -> list[dict]:
    cnt, commits = Counter(), Counter()
    for u in store.utterances():
        for o in set(u["orgs"]) | ({u["org"]} if u["org"] else set()):
            cnt[o] += 1
        if u["is_commitment"] and u["org"]:
            commits[u["org"]] += 1
    return [{"name": n, "aliases": list(ORGANIZATIONS[n]), "mentions": cnt[n],
             "commitments": commits[n]} for n in ORGANIZATIONS]


def issue_network(store: Store, min_weight: int = 1) -> dict:
    """쟁점–기관–위원 관계망(시각화용)."""
    nodes: dict[str, dict] = {}
    edges: Counter = Counter()

    def node(nid: str, label: str, kind: str):
        n = nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, "weight": 0})
        n["weight"] += 1

    for u in store.utterances():
        orgs_ = set(u["orgs"]) | ({u["org"]} if u["org"] else set())
        for i in u["issues"]:
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


def issue_by_session(store: Store) -> dict:
    """회기 × 쟁점 언급량(히트맵용). 하위 쟁점만 집계."""
    rows = store.conn.execute(
        "SELECT m.session_no, u.issues_json FROM utterances u JOIN meetings m ON m.id=u.meeting_id "
        "WHERE m.session_no IS NOT NULL AND u.issues_json != '{}'").fetchall()
    matrix: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for iid, n in json.loads(r["issues_json"]).items():
            if ISSUES[iid][1]:
                matrix[iid][r["session_no"]] += n
    sessions = sorted({sn for c in matrix.values() for sn in c})
    order = sorted(matrix, key=lambda i: -sum(matrix[i].values()))
    return {
        "sessions": sessions,
        "issues": [{"id": i, "label": ISSUES[i][0], "parent": ISSUES[i][1],
                    "total": sum(matrix[i].values()),
                    "by_session": {str(k): v for k, v in matrix[i].items()}} for i in order],
    }


def issue_detail(store: Store, issue_id: str, session: int | None = None, limit: int = 20) -> dict:
    """쟁점 상세: 회기별 추이, 관련 기관·위원, 대표 질의(요약)와 정부 약속."""
    if issue_id not in ISSUES:
        raise KeyError(issue_id)
    label, parent, _ = ISSUES[issue_id]
    ids = {issue_id} | {i for i, v in ISSUES.items() if v[1] == issue_id}
    focus = {k for i in ids for k in ISSUES[i][2]}
    seen, uniq = set(), []
    for i in ids:
        for u in store.utterances(issue=i, session=session):
            if u["id"] not in seen:
                seen.add(u["id"]); uniq.append(u)
    by_session = Counter(u["session_no"] for u in uniq if u["session_no"])
    orgs, members = Counter(), Counter()
    for u in uniq:
        orgs.update(set(u["orgs"]) | ({u["org"]} if u["org"] else set()))
        if u["speaker_type"] == "member":
            members[u["speaker_name"]] += 1
    weight = lambda u: sum(u["issues"].get(i, 0) for i in ids)
    questions = sorted((u for u in uniq if u["is_question"]),
                       key=lambda u: (-weight(u), -(len(u["text"]))))
    commitments = sorted((u for u in uniq if u["is_commitment"]),
                         key=lambda u: (u["meeting_date"] or "", weight(u)), reverse=True)

    def view(u: dict) -> dict:
        return {"meeting_id": u["meeting_id"], "date": u["meeting_date"], "session": u["session_no"],
                "speaker": u["speaker_name"] + " 위원" if u["speaker_type"] == "member"
                else f"{u['speaker_role']} {u['speaker_name']}",
                "speaker_type": u["speaker_type"],
                "text": " ".join(extractive_summary(u["text"], 2, focus=focus)) or truncate(u["text"], 220)}

    return {
        "id": issue_id, "label": label, "parent": ISSUES[parent][0] if parent else None,
        "dialogue": issue_dialogue(store, questions[:12], commitments[:40], focus),
        "children": [{"id": i, "label": v[0]} for i, v in ISSUES.items() if v[1] == issue_id],
        "keywords": sorted(focus), "count": len(uniq),
        "meetings": len({u["meeting_id"] for u in uniq}),
        "by_session": [{"session": k, "count": v} for k, v in sorted(by_session.items())],
        "orgs": [{"name": n, "count": c} for n, c in orgs.most_common(8)],
        "members": [{"name": n, "count": c} for n, c in members.most_common(8)],
        "questions": [view(u) for u in questions[:limit]],
        "commitments": [view(u) for u in commitments[:limit]],
    }


def _neighbors(store: Store, u: dict, before: int = 8, after: int = 8) -> list[dict]:
    rows = store.conn.execute(
        "SELECT u.*, m.date AS meeting_date, m.session_no AS session_no FROM utterances u "
        "JOIN meetings m ON m.id=u.meeting_id WHERE u.meeting_id=? AND u.idx BETWEEN ? AND ? "
        "ORDER BY u.idx", (u["meeting_id"], u["idx"] - before, u["idx"] + after)).fetchall()
    return [store._utt(r) for r in rows]


def issue_dialogue(store: Store, questions: list[dict], commitments: list[dict],
                   focus: set[str], limit: int = 12) -> list[dict]:
    """쟁점 대화: 정부 약속은 그 앞의 위원 질의와, 대표 질의는 뒤따른 정부 답변과 묶는다.
    화면에서 질의는 왼쪽, 답변·약속은 오른쪽 말풍선으로 보인다."""
    threads: dict[tuple, dict] = {}

    def add(q: dict | None, a: dict | None) -> None:
        key = (q or a)["meeting_id"], (q or a)["idx"]
        if key in threads and not (a and a["is_commitment"]):
            return
        threads[key] = {"q": q, "a": a}

    for c in commitments:
        near = _neighbors(store, c, before=30, after=0)
        # 바로 앞 질의(물음)를 우선, 없으면 짧은 맞장구('이상입니다')가 아닌 위원 발언
        members = [u for u in reversed(near[:-1]) if u["speaker_type"] == "member"]
        q = next((u for u in members if u["is_question"]), None) or \
            next((u for u in members if len(u["text"]) >= 40), None)
        add(q, c)
    for q in questions:
        near = [u for u in _neighbors(store, q, before=0) if u["idx"] > q["idx"]]
        a = None
        for u in near:
            if u["speaker_type"] == "member":
                break
            if u["speaker_type"] in ("official", "witness"):
                a = u
                break
        add(q, a)

    def qv(u: dict) -> dict:
        return {"speaker": u["speaker_name"] + " 위원",
                "text": " ".join(extractive_summary(u["text"], 2, focus=focus)) or truncate(u["text"], 220)}

    def av(u: dict) -> dict:
        text = (" ".join(commitment_sentences(u["text"])[:2]) if u["is_commitment"] else
                " ".join(extractive_summary(u["text"], 2, focus=focus)))
        return {"speaker": f"{u['speaker_role']} {u['speaker_name']}", "org": u["org"],
                "commitment": u["is_commitment"], "text": truncate(text or u["text"], 260)}

    # 질의와 약속이 모두 있는 대화 > 질의와 답변 > 한쪽만 있는 것(업무보고 중 약속 등)
    rank = lambda t: (2 if t["q"] and t["a"] and t["a"]["is_commitment"] else
                      1 if t["q"] and t["a"] else 0)
    chosen = sorted(threads.values(), key=rank, reverse=True)[:limit]
    out = []
    for t in chosen:
        base = t["q"] or t["a"]
        out.append({"meeting_id": base["meeting_id"], "date": base["meeting_date"],
                    "session": base["session_no"], "idx": base["idx"],
                    "question": qv(t["q"]) if t["q"] else None,
                    "answer": av(t["a"]) if t["a"] else None})
    out.sort(key=lambda d: (d["date"] or "", -d["idx"]), reverse=True)
    return out


def overview(store: Store, recent: int = 6) -> dict:
    """대시보드용: 최근 회의(요약 한 줄·핵심 쟁점), 최근 정부 약속."""
    out_m = []
    for m in store.meetings()[:recent]:
        s = store.summary(m["id"], "rule") or {}
        out_m.append({"id": m["id"], "date": m["date"], "title": m["title"],
                      "session_no": m["session_no"], "agendas": m["agendas"][:3],
                      "n_agendas": len(m["agendas"]),
                      "issues": [i["label"] for i in (s.get("key_issues") or [])[:3]],
                      "headline": (s.get("key_points") or [""])[0]})
    rows = store.conn.execute(
        "SELECT u.*, m.date AS meeting_date, m.title AS meeting_title, m.session_no AS session_no "
        "FROM utterances u JOIN meetings m ON m.id=u.meeting_id "
        "WHERE u.is_commitment=1 ORDER BY m.date DESC, u.idx DESC LIMIT ?", (recent * 2,)).fetchall()
    return {"meetings": out_m, "commitments": [commitment_view(store._utt(r)) for r in rows]}
