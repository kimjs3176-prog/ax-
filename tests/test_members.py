from fastapi.testclient import TestClient

from agrisea import members
from agrisea.web.app import create_app


def test_member_profiles_all_meetings(store):
    p = members.member_profiles(store)
    rows = members.summary_rows(p)
    assert rows and rows[0]["counts"]["questions"] >= rows[-1]["counts"]["questions"]
    kim = p["members"]["김가람"]
    assert {"쌀값", "양곡관리법"} & {k["word"] for k in kim["keywords"]}
    assert kim["top_issues"][0]["label"] == "쌀·식량안보"
    assert kim["perspectives"] and kim["headline"].startswith("「쌀·식량안보」")
    eq = kim["expected_questions"]
    assert eq and all(q["question"] and q["basis"]["meeting_id"] for q in eq)
    assert any(q["type"] == "이행 점검" for q in eq)  # 받아낸 정부 약속의 이행 점검


def test_member_profiles_selected_meetings(store):
    one = members.member_profiles(store, meeting_ids=["SAMPLE-2025-1014"])
    assert one["scope"]["n_meetings"] == 1
    for prof in one["members"].values():
        assert prof["meetings"] == ["SAMPLE-2025-1014"]
    assert len(one["members"]) < len(members.member_profiles(store)["members"]) + 1


def test_keyword_filter_drops_fillers_and_names(store):
    v = members.vocab(store)
    for w in ("굉장히", "그러니까", "말이지", "된다"):
        assert w not in v["words"]
    assert not members._bad_token("양곡관리법", set())
    assert members._bad_token("산림청차장", set()) and members._bad_token("전무이사홍길동", {"홍길동"})


def test_member_endpoints(store, settings):
    c = TestClient(create_app(settings, store))
    rows = c.get("/api/members").json()["members"]
    name = rows[0]["name"]
    d = c.get(f"/api/members/{name}").json()
    assert d["name"] == name and d["expected_questions"] and d["keywords"]
    part = c.get("/api/members", params={"meetings": "SAMPLE-2025-1014"}).json()
    assert part["scope"]["meetings"] == ["SAMPLE-2025-1014"]
    assert c.get("/api/members/없는위원").status_code == 404
