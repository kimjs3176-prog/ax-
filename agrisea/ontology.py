"""저장소 → RDF 지식그래프(온톨로지 인스턴스) 변환 및 SPARQL 질의."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from rdflib import RDF, RDFS, SKOS, XSD, Graph, Literal, Namespace, URIRef

from .config import COMMITTEE_NAME, ROOT
from .lexicon import ISSUES, ORGANIZATIONS, match_organizations
from .store import Store

AG = Namespace("https://w3id.org/agrisea/ontology#")
AGD = Namespace("https://w3id.org/agrisea/id/")
SCHEMA_PATH = ROOT / "ontology" / "agrisea.ttl"


def _slug(text: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", text).strip("_")
    if len(s) > 60:
        s = s[:50] + "_" + hashlib.md5(text.encode()).hexdigest()[:8]
    return s or hashlib.md5(text.encode()).hexdigest()[:12]


def meeting_uri(mid: str) -> URIRef:
    return AGD[f"meeting/{_slug(mid)}"]


def person_uri(name: str, stype: str) -> URIRef:
    return AGD[f"person/{stype}/{_slug(name)}"]


def org_uri(name: str) -> URIRef:
    return AGD[f"org/{_slug(name)}"]


def issue_uri(iid: str) -> URIRef:
    return AGD[f"issue/{iid}"]


def meeting_class(m: dict) -> URIRef:
    hay = f"{m.get('title', '')} {m.get('class_name', '')} {' '.join(m.get('agendas', []))}"
    if "국정감사" in hay:
        return AG.AuditMeeting
    if "소위" in hay:
        return AG.SubcommitteeMeeting
    return AG.PlenaryCommitteeMeeting


PERSON_CLASS = {
    "member": AG.Legislator, "chair": AG.Chair, "official": AG.GovernmentOfficial,
    "witness": AG.Witness, "reference": AG.Witness, "staff": AG.Staff, "other": AG.Person,
}


def new_graph(with_schema: bool = True) -> Graph:
    g = Graph()
    g.bind("ag", AG)
    g.bind("agd", AGD)
    g.bind("skos", SKOS)
    if with_schema and SCHEMA_PATH.exists():
        g.parse(SCHEMA_PATH, format="turtle")
    return g


def add_vocabulary(g: Graph) -> None:
    committee = AGD["committee/agrisea"]
    g.add((committee, RDF.type, AG.Committee))
    g.add((committee, RDFS.label, Literal(COMMITTEE_NAME, lang="ko")))
    for iid, (label, parent, kws) in ISSUES.items():
        u = issue_uri(iid)
        g.add((u, RDF.type, AG.Issue))
        g.add((u, SKOS.inScheme, AG.IssueScheme))
        g.add((u, SKOS.prefLabel, Literal(label, lang="ko")))
        g.add((u, RDFS.label, Literal(label, lang="ko")))
        if parent:
            g.add((u, SKOS.broader, issue_uri(parent)))
            g.add((issue_uri(parent), SKOS.narrower, u))
        else:
            g.add((AG.IssueScheme, SKOS.hasTopConcept, u))
        for k in kws:
            g.add((u, AG.keyword, Literal(k, lang="ko")))
    for name, aliases in ORGANIZATIONS.items():
        u = org_uri(name)
        g.add((u, RDF.type, AG.AuditedAgency))
        g.add((u, RDFS.label, Literal(name, lang="ko")))
        for a in aliases:
            g.add((u, SKOS.altLabel, Literal(a, lang="ko")))


def build_graph(store: Store, with_schema: bool = True) -> Graph:
    g = new_graph(with_schema)
    add_vocabulary(g)
    committee = AGD["committee/agrisea"]

    for m in store.meetings():
        mu = meeting_uri(m["id"])
        g.add((mu, RDF.type, meeting_class(m)))
        g.add((mu, RDF.type, AG.Meeting))
        g.add((mu, RDFS.label, Literal(m["title"] or m["id"], lang="ko")))
        g.add((mu, AG.heldBy, committee))
        if m["date"]:
            g.add((mu, AG.meetingDate, Literal(m["date"], datatype=XSD.date)))
        if m["dae"] and str(m["dae"]).isdigit():
            g.add((mu, AG.assemblyTerm, Literal(int(m["dae"]), datatype=XSD.integer)))
        if m["session_no"]:
            su = AGD[f"session/{m['dae'] or 'x'}_{m['session_no']}"]
            g.add((su, RDF.type, AG.Session))
            g.add((su, RDFS.label, Literal(f"제{m['session_no']}회 국회", lang="ko")))
            g.add((su, AG.sessionNumber, Literal(m["session_no"], datatype=XSD.integer)))
            g.add((mu, AG.inSession, su))
        if m["conf_no"]:
            g.add((mu, AG.meetingNumber, Literal(m["conf_no"], datatype=XSD.integer)))
        if m["class_name"]:
            g.add((mu, AG.meetingKind, Literal(m["class_name"], lang="ko")))
        for prop, key in ((AG.pdfURL, "pdf_url"), (AG.minutesURL, "link_url"),
                          (AG.vodURL, "vod_url")):
            if m.get(key):
                g.add((mu, prop, Literal(m[key], datatype=XSD.anyURI)))
        if m["is_sample"]:
            g.add((mu, AG.isSampleData, Literal(True)))

        agenda_uris = []
        for i, a in enumerate(m["agendas"]):
            au = AGD[f"agenda/{_slug(m['id'])}/{i}"]
            agenda_uris.append(au)
            g.add((au, RDF.type, AG.AgendaItem))
            g.add((au, RDFS.label, Literal(a, lang="ko")))
            g.add((au, AG.orderIndex, Literal(i, datatype=XSD.integer)))
            g.add((mu, AG.hasAgendaItem, au))
            for org in match_organizations(a):
                if meeting_class(m) == AG.AuditMeeting:
                    g.add((mu, AG.audited, org_uri(org)))
                g.add((au, AG.mentionsOrganization, org_uri(org)))

        last_question: URIRef | None = None
        last_q_orgs: list[str] = []
        for u in store.utterances(meeting_id=m["id"]):
            uu = AGD[f"utt/{_slug(m['id'])}/{u['idx']}"]
            pu = person_uri(u["speaker_name"], u["speaker_type"])
            g.add((pu, RDF.type, PERSON_CLASS.get(u["speaker_type"], AG.Person)))
            g.add((pu, RDFS.label, Literal(u["speaker_name"], lang="ko")))
            g.add((pu, AG.role, Literal(u["speaker_role"], lang="ko")))
            if u["speaker_type"] in ("member", "chair"):
                g.add((pu, AG.memberOf, committee))
            if u["org"]:
                g.add((pu, AG.affiliatedWith, org_uri(u["org"])))

            g.add((uu, RDF.type, AG.Utterance))
            g.add((uu, AG.inMeeting, mu))
            g.add((mu, AG.hasUtterance, uu))
            g.add((uu, AG.spokenBy, pu))
            g.add((uu, AG.orderIndex, Literal(u["idx"], datatype=XSD.integer)))
            g.add((uu, AG.text, Literal(u["text"], lang="ko")))
            if u["agenda_idx"] is not None and u["agenda_idx"] < len(agenda_uris):
                g.add((uu, AG.onAgendaItem, agenda_uris[u["agenda_idx"]]))
            for iid, n in u["issues"].items():
                g.add((uu, AG.concernsIssue, issue_uri(iid)))
                g.add((mu, AG.concernsIssue, issue_uri(iid)))
            for org in u["orgs"]:
                g.add((uu, AG.mentionsOrganization, org_uri(org)))

            if u["is_question"]:
                g.add((uu, RDF.type, AG.Question))
                last_question, last_q_orgs = uu, list(u["orgs"])
                for org in last_q_orgs:
                    g.add((uu, AG.addressedTo, org_uri(org)))
            elif u["speaker_type"] in ("official", "witness") and last_question is not None:
                g.add((uu, RDF.type, AG.Answer))
                g.add((uu, AG.respondsTo, last_question))
                if u["org"]:
                    g.add((last_question, AG.addressedTo, org_uri(u["org"])))
            if u["is_data_request"]:
                g.add((uu, RDF.type, AG.DataRequest))
            if u["is_commitment"]:
                g.add((uu, RDF.type, AG.Commitment))
                g.add((uu, RDF.type, AG.Answer))
                if u["org"]:
                    g.add((uu, AG.committedBy, org_uri(u["org"])))
    return g


def save_graph(g: Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(path, format="turtle")


# 자주 쓰는 질의(웹 UI 'SPARQL' 탭의 예시)
PRESET_QUERIES: dict[str, str] = {
    "쟁점별 언급 회의 수": """
PREFIX ag: <https://w3id.org/agrisea/ontology#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?issueLabel (COUNT(DISTINCT ?m) AS ?meetings) (COUNT(DISTINCT ?u) AS ?utterances)
WHERE {
  ?u a ag:Utterance ; ag:concernsIssue ?issue ; ag:inMeeting ?m .
  ?issue skos:prefLabel ?issueLabel .
}
GROUP BY ?issueLabel ORDER BY DESC(?utterances)""",
    "기관별 이행약속 답변": """
PREFIX ag: <https://w3id.org/agrisea/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?orgLabel ?date ?speaker ?text
WHERE {
  ?c a ag:Commitment ; ag:committedBy ?org ; ag:spokenBy ?p ; ag:text ?text ; ag:inMeeting ?m .
  ?org rdfs:label ?orgLabel . ?p rdfs:label ?speaker . ?m ag:meetingDate ?date .
}
ORDER BY ?orgLabel DESC(?date)""",
    "위원별 질의-답변 쌍": """
PREFIX ag: <https://w3id.org/agrisea/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?member ?question ?answerer ?answer
WHERE {
  ?a ag:respondsTo ?q ; ag:spokenBy ?ap ; ag:text ?answer .
  ?q ag:spokenBy ?qp ; ag:text ?question .
  ?qp a ag:Legislator ; rdfs:label ?member .
  ?ap rdfs:label ?answerer .
}
LIMIT 100""",
    "하위 쟁점까지 포함한 '해양·수산' 발언": """
PREFIX ag: <https://w3id.org/agrisea/ontology#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?issueLabel ?speaker ?text
WHERE {
  ?issue skos:broader* ?root . ?root skos:prefLabel "해양·수산"@ko .
  ?issue skos:prefLabel ?issueLabel .
  ?u ag:concernsIssue ?issue ; ag:text ?text ; ag:spokenBy/rdfs:label ?speaker .
}
LIMIT 100""",
    "국정감사 회의와 피감기관": """
PREFIX ag: <https://w3id.org/agrisea/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?date ?title ?agency
WHERE {
  ?m a ag:AuditMeeting ; rdfs:label ?title ; ag:meetingDate ?date .
  OPTIONAL { ?m ag:audited/rdfs:label ?agency }
}
ORDER BY DESC(?date)""",
}


def run_sparql(g: Graph, query: str, limit: int = 500) -> dict:
    result = g.query(query)
    if result.type == "ASK":
        return {"columns": ["ask"], "rows": [[bool(result.askAnswer)]]}
    if result.type == "CONSTRUCT" or result.type == "DESCRIBE":
        rows = [[str(s), str(p), str(o)] for s, p, o in list(result.graph)[:limit]]
        return {"columns": ["s", "p", "o"], "rows": rows}
    cols = [str(v) for v in result.vars]
    rows = []
    for i, r in enumerate(result):
        if i >= limit:
            break
        rows.append([None if v is None else str(v) for v in r])
    return {"columns": cols, "rows": rows}
