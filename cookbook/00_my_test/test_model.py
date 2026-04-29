"""Tests for Model serialization, save, and load from DB."""

from agno.db.postgres import PostgresDb
from agno.models._storage import from_dict, get_component_id, list_models, load, to_dict
from agno.models.anthropic import Claude
from agno.models.base import Model
from agno.models.openai import OpenAIChat

DB_URL = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(id="basic-db", db_url=DB_URL)


def test_from_dict_infers_openai_class():
    original = OpenAIChat(id="gpt-4o", temperature=0.5, max_tokens=512)
    data = to_dict(original)

    restored = from_dict(Model, data)

    assert type(restored).__name__ == "OpenAIChat"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    print(f"test_from_dict_infers_openai_class PASS (type={type(restored).__name__})")


def test_from_dict_infers_claude_class():
    original = Claude(id="claude-sonnet-4-5-20250929", temperature=0.3, max_tokens=1024)
    data = to_dict(original)

    restored = from_dict(Model, data)

    assert type(restored).__name__ == "Claude"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    print(f"test_from_dict_infers_claude_class PASS (type={type(restored).__name__})")


def test_component_id_uniqueness():
    model = OpenAIChat(id="gpt-4o")
    id1 = get_component_id(model)
    id2 = get_component_id(model)

    assert id1.startswith("model:OpenAIChat:gpt-4o:")
    assert id2.startswith("model:OpenAIChat:gpt-4o:")
    assert id1 != id2
    print(f"test_component_id_uniqueness PASS ({id1} vs {id2})")


def test_credentials_not_serialized_and_url_is_serialized():
    openai_model = OpenAIChat(
        id="gpt-4o",
        api_key="sk-should-not-persist",
        base_url="https://my-proxy.example.com/v1",
    )
    openai_data = to_dict(openai_model)

    assert "api_key" in openai_data
    assert openai_data["base_url"] == "https://my-proxy.example.com/v1"

    claude_model = Claude(
        id="claude-sonnet-4-5-20250929",
        api_key="sk-secret-should-not-persist",
        auth_token="tok-should-not-persist",
    )
    claude_data = to_dict(claude_model)

    assert "api_key" in claude_data
    assert "auth_token" in claude_data
    print("test_credentials_not_serialized_and_url_is_serialized PASS")


def test_save_returns_component_id_and_version():
    model = OpenAIChat(id="gpt-4o", temperature=0.7, max_tokens=1024)

    component_id, version = model.save(db, model_name="test-gpt4o", label="save-test")

    assert component_id.startswith("model:OpenAIChat:gpt-4o:")
    assert version is not None
    print(f"test_save_returns_component_id_and_version PASS (component_id={component_id}, version={version})")


def test_save_and_blind_load_openai():
    original = OpenAIChat(id="gpt-4o", temperature=0.3, max_tokens=256)
    component_id, _ = original.save(db, label="openai-blind-load-test")

    restored = load(Model, component_id, db, label="openai-blind-load-test")

    assert restored is not None
    assert type(restored).__name__ == "OpenAIChat"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    print(f"test_save_and_blind_load_openai PASS (component_id={component_id})")


def test_save_and_blind_load_claude():
    original = Claude(
        id="claude-sonnet-4-5-20250929",
        api_key="sk-secret-should-not-persist",
        temperature=0.4,
        max_tokens=2048,
    )
    component_id, _ = original.save(db, label="claude-blind-load-test")

    restored = load(Model, component_id, db, label="claude-blind-load-test")

    assert restored is not None
    assert type(restored).__name__ == "Claude"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    assert getattr(restored, "api_key", None) == "sk-secret-should-not-persist"
    print(f"test_save_and_blind_load_claude PASS (component_id={component_id})")


def test_list_models():
    OpenAIChat(id="gpt-4o", temperature=0.1).save(db)
    OpenAIChat(id="gpt-4o", temperature=0.9).save(db)

    models, total = list_models(db, limit=10)

    assert total >= 2
    assert len(models) > 0
    print(f"test_list_models PASS (total={total})")


if __name__ == "__main__":
    test_from_dict_infers_openai_class()
    test_from_dict_infers_claude_class()
    test_component_id_uniqueness()
    test_credentials_not_serialized_and_url_is_serialized()
    test_save_returns_component_id_and_version()
    test_save_and_blind_load_openai()
    test_save_and_blind_load_claude()
    test_list_models()
    print("All tests passed.")
