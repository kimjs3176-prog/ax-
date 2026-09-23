from rdflib import RDF

from agrisea.ontology import AG, PRESET_QUERIES, build_graph, kg_from_store, run_sparql


def test_graph_contains_core_classes(store):
    g = build_graph(store)
    assert len(list(g.subjects(RDF.type, AG.AuditMeeting))) == 3
    assert len(list(g.subjects(RDF.type, AG.PlenaryCommitteeMeeting))) == 1
    assert len(list(g.subjects(RDF.type, AG.Commitment))) > 0
    assert len(list(g.subjects(AG.respondsTo, None))) > 0


def test_presets_run(store):
    g = kg_from_store(store)
    for name, q in PRESET_QUERIES.items():
        res = run_sparql(g, q)
        assert res["rows"], name


def test_skos_hierarchy_query(store):
    g = kg_from_store(store)
    res = run_sparql(g, PRESET_QUERIES["하위 쟁점까지 포함한 '해양·수산' 발언"])
    labels = {r[0] for r in res["rows"]}
    assert "수산업·어촌" in labels


def test_ask_and_construct(store):
    kg = kg_from_store(store)
    assert run_sparql(kg, "ASK { ?s a <https://w3id.org/agrisea/ontology#Commitment> }")["rows"] == [[True]]
    res = run_sparql(kg, "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 5")
    assert res["columns"] == ["s", "p", "o"] and len(res["rows"]) == 5


def test_core_graph_is_smaller(store):
    assert len(build_graph(store, core=True, text_limit=50)) < len(build_graph(store))
