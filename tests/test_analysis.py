from agrisea.analysis import briefing, briefing_markdown, issue_overview, summarize_meeting
from agrisea.nlp import extractive_summary, tokenize


def test_tokenize_strips_josa_and_keeps_domain_terms():
    toks = tokenize("쌀값이 떨어지고 수산물 소비가 위축되었습니다")
    assert "쌀값" in toks and "수산물 소비" in toks


def test_extractive_summary_keeps_order():
    text = "첫 문장은 인사입니다. 쌀값 폭락으로 농가소득이 20% 줄었습니다. 날씨가 좋습니다. 양곡관리법 개정이 필요합니다."
    out = extractive_summary(text, 2)
    assert out == ["쌀값 폭락으로 농가소득이 20% 줄었습니다.", "양곡관리법 개정이 필요합니다."]


def test_store_search(store):
    assert store.search("시장격리")  # FTS(3글자 이상)
    assert store.search("쌀값")  # LIKE(2글자)
    assert all("오염수" in r["text"] for r in store.search("오염수"))
    assert store.search("존재하지않는단어") == []


def test_summarize_meeting(store):
    s = summarize_meeting(store, "SAMPLE-2025-1024")
    assert s["key_issues"][0]["label"] == "스마트농업·R&D"
    assert len(s["agenda_summaries"]) == 3
    assert s["agenda_summaries"][1]["speakers"][0] == "정하늘"
    assert s["commitments"] and s["qa_highlights"]


def test_briefing_by_org_and_issue(store):
    b = briefing(store, org="한국농업기술진흥원")
    assert b["counts"]["commitments"] == 2
    assert {m["name"] for m in b["active_members"]} == {"정하늘"}
    b2 = briefing(store, issue="sea")
    assert all(m["date"] == "2025-10-20" for m in b2["meetings"])
    md = briefing_markdown(b2)
    assert "이행약속 추적표" in md and "해양수산부장관" in md


def test_issue_overview_rolls_up(store):
    ov = {i["id"]: i for i in issue_overview(store)}
    assert ov["sea"]["count"] >= ov["fishery"]["count"] > 0
