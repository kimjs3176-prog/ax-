import pytest

from agrisea.api_client import (AssemblyAPIError, AssemblyClient, is_target_committee,
                                normalize_row)

SERVICE = "ncwgseseafwbuheph"


def _payload(rows, total=None):
    return {SERVICE: [
        {"head": [{"list_total_count": total if total is not None else len(rows)},
                  {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다."}}]},
        {"row": rows},
    ]}


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return FakeResp(self.pages[params["pIndex"] - 1])


ROW_A1 = {"CONFER_NUM": "100", "TITLE": "제418회 국회(정기회) 제1차 농림축산식품해양수산위원회",
          "COMM_NAME": "농림축산식품해양수산위원회", "CONF_DATE": "20241007", "DAE_NUM": "22",
          "SUB_NAME": "농림축산식품부에 대한 국정감사", "PDF_LINK_URL": "http://x/a.pdf"}
ROW_A2 = {**ROW_A1, "SUB_NAME": "농촌진흥청에 대한 국정감사", "PDF_LINK_URL": ""}
ROW_B = {"CONFER_NUM": "200", "TITLE": "제418회 국회 제1차 교육위원회",
         "COMM_NAME": "교육위원회", "CONF_DATE": "2024-10-07", "SUB_NAME": "교육부 국정감사"}


def test_normalize_row_parses_session_and_date():
    r = normalize_row(ROW_A1)
    assert r["meeting_id"] == "100"
    assert r["date"] == "2024-10-07"
    assert r["session_no"] == 418 and r["conf_no"] == 1
    assert r["agenda"] == "농림축산식품부에 대한 국정감사"
    assert is_target_committee(r)
    assert not is_target_committee(normalize_row(ROW_B))


def test_committee_meetings_groups_agendas_and_filters(settings):
    sess = FakeSession([_payload([ROW_A1, ROW_B], total=3), _payload([ROW_A2], total=3)])
    client = AssemblyClient(settings, session=sess, sleep=0)
    ms = client.committee_meetings(DAE_NUM="22", CONF_DATE="20241007")
    assert len(ms) == 1
    assert ms[0]["agendas"] == ["농림축산식품부에 대한 국정감사", "농촌진흥청에 대한 국정감사"]
    assert ms[0]["pdf_url"] == "http://x/a.pdf"
    assert [c["pIndex"] for c in sess.calls] == [1, 2]
    assert sess.calls[0]["KEY"] == "TEST" and sess.calls[0]["DAE_NUM"] == "22"
    assert sess.calls[0]["CONF_DATE"] == "2024-10-07"  # 구분자 없는 입력도 API 형식으로 변환


def test_required_params_and_date_format(settings):
    from agrisea.api_client import normalize_conf_date
    client = AssemblyClient(settings, session=FakeSession([]))
    with pytest.raises(ValueError, match="CONF_DATE"):
        client.committee_meetings(DAE_NUM="22")
    assert normalize_conf_date("202609") == "2026-09"
    assert normalize_conf_date("2026.9.1") == "2026-09-01"
    with pytest.raises(ValueError):
        normalize_conf_date("26-09-01")


def test_no_data_and_error_codes(settings):
    client = AssemblyClient(settings, session=FakeSession([]))
    assert client.parse_response({"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}}).total == 0
    with pytest.raises(AssemblyAPIError) as e:
        client.parse_response({"RESULT": {"CODE": "ERROR-300", "MESSAGE": "필수 값 누락"}})
    assert e.value.code == "ERROR-300"


def test_requires_key(tmp_path):
    from agrisea.config import Settings
    with pytest.raises(ValueError):
        AssemblyClient(Settings(api_key="", data_dir=tmp_path))


def test_network_error_redacts_key(settings):
    import requests

    class Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"failed {url}?KEY={params['KEY']}&pIndex=1")

    client = AssemblyClient(settings, session=Boom(), retries=0)
    with pytest.raises(AssemblyAPIError) as e:
        client.fetch_page()
    assert "TEST" not in str(e.value) and "***" in str(e.value)


def test_transient_error_is_retried(settings, monkeypatch):
    import requests
    monkeypatch.setattr("agrisea.api_client.time.sleep", lambda s: None)
    calls = []

    class Flaky:
        def get(self, url, params=None, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise requests.ConnectionError("reset")
            return FakeResp(_payload([ROW_A1]))

    page = AssemblyClient(settings, session=Flaky()).fetch_page()
    assert len(calls) == 2 and page.total == 1
