"""Tests for Knowledge serialization, save, and load from DB."""

from agno.db.postgres import PostgresDb
from agno.knowledge._storage import from_dict, get_component_id, list_knowledge, load, to_dict
from agno.knowledge.knowledge import Knowledge, get_knowledge_by_id

DB_URL = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(id="basic-db", db_url=DB_URL)


def test_to_dict_contains_class_path():
    kb = Knowledge(name="test-kb", max_results=5)
    data = to_dict(kb)

    assert data["class_path"] == "agno.knowledge.knowledge.Knowledge"
    assert data["name"] == "test-kb"
    assert data["max_results"] == 5
    print("test_to_dict_contains_class_path PASS")


def test_from_dict_roundtrip():
    original = Knowledge(name="roundtrip-kb", max_results=20, description="test description")
    data = to_dict(original)
    restored = from_dict(Knowledge, data)

    assert type(restored).__name__ == "Knowledge"
    assert restored.name == original.name
    assert restored.max_results == original.max_results
    assert restored.description == original.description
    print(f"test_from_dict_roundtrip PASS (type={type(restored).__name__})")


def test_component_id_uniqueness():
    kb = Knowledge(name="kb-a")
    id1 = get_component_id(kb)
    id2 = get_component_id(kb)

    assert id1.startswith("knowledge:Knowledge:kb-a:")
    assert id1 != id2
    print(f"test_component_id_uniqueness PASS ({id1} vs {id2})")


def test_component_id_uses_name():
    kb = Knowledge(name="my-knowledge")
    component_id = get_component_id(kb)

    assert "my-knowledge" in component_id
    print(f"test_component_id_uses_name PASS ({component_id})")


def test_save_returns_component_id_and_version():
    kb = Knowledge(name="save-test-kb", max_results=10)
    component_id, version = kb.save(db, label="save-test")

    assert component_id.startswith("knowledge:Knowledge:")
    assert version is not None
    print(f"test_save_returns_component_id_and_version PASS (component_id={component_id}, version={version})")


def test_save_and_load():
    original = Knowledge(name="load-test-kb", max_results=15, description="load test")
    component_id, _ = original.save(db, label="load-test")

    restored = load(Knowledge, component_id, db, label="load-test")

    assert restored is not None
    assert type(restored).__name__ == "Knowledge"
    assert restored.name == original.name
    assert restored.max_results == original.max_results
    assert restored.description == original.description
    print(f"test_save_and_load PASS (component_id={component_id})")


def test_get_knowledge_by_id():
    original = Knowledge(name="get-by-id-kb", max_results=8)
    component_id, _ = original.save(db, label="get-by-id-test")

    restored = get_knowledge_by_id(component_id, db, label="get-by-id-test")

    assert restored is not None
    assert restored.name == original.name
    assert restored.max_results == original.max_results
    print(f"test_get_knowledge_by_id PASS (component_id={component_id})")


def test_list_knowledge():
    Knowledge(name="list-kb-1", max_results=1).save(db)
    Knowledge(name="list-kb-2", max_results=2).save(db)

    components, total = list_knowledge(db, limit=10)

    assert total >= 2
    assert len(components) > 0
    print(f"test_list_knowledge PASS (total={total})")


if __name__ == "__main__":
    test_to_dict_contains_class_path()
    test_from_dict_roundtrip()
    test_component_id_uniqueness()
    test_component_id_uses_name()
    test_save_returns_component_id_and_version()
    test_save_and_load()
    test_get_knowledge_by_id()
    test_list_knowledge()
    print("All tests passed.")
