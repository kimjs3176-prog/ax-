from rdflib import RDF

from agrisea.ontology import AG, PRESET_QUERIES, build_graph, run_sparql


def test_graph_contains_core_classes(store):
    g = build_graph(store)
    assert len(list(g.subjects(RDF.type, AG.AuditMeeting))) == 3
    assert len(list(g.subjects(RDF.type, AG.PlenaryCommitteeMeeting))) == 1
    assert len(list(g.subjects(RDF.type, AG.Commitment))) > 0
    assert len(list(g.subjects(AG.respondsTo, None))) > 0


def test_presets_run(store):
    g = build_graph(store)
    for name, q in PRESET_QUERIES.items():
        res = run_sparql(g, q)
        assert res["rows"], name


def test_skos_hierarchy_query(store):
    g = build_graph(store)
    res = run_sparql(g, PRESET_QUERIES["하위 쟁점까지 포함한 '해양·수산' 발언"])
    labels = {r[0] for r in res["rows"]}
    assert "수산업·어촌" in labels
