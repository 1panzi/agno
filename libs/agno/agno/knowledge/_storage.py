"""Database persistence helpers for Knowledge."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type
from uuid import uuid4

from pydantic import BaseModel

from agno.db.base import BaseDb, ComponentType
from agno.db.utils import resolve_db_from_config
from agno.utils.log import log_error, log_warning

if TYPE_CHECKING:
    from agno.knowledge.knowledge import Knowledge
    from agno.registry.registry import Registry

_SKIP_FIELDS = frozenset()
_SENSITIVE_SUFFIXES = ("_key", "_secret", "_token", "_password", "_credential")


def get_component_id(knowledge: "Knowledge") -> str:
    uid = str(uuid4())[:8]
    name = getattr(knowledge, "name", None) or knowledge.__class__.__name__
    return f"knowledge:{knowledge.__class__.__name__}:{name}:{uid}"


def _is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _class_path(obj: Any) -> str:
    return f"{obj.__class__.__module__}.{obj.__class__.__name__}"


def _serialized_scalar(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _serialize_object(obj: Any) -> Optional[Dict[str, Any]]:
    data: Dict[str, Any] = {"class_path": _class_path(obj)}

    if isinstance(obj, BaseModel):
        for key, value in obj.model_dump(exclude_none=True).items():
            if any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
                continue
            value = _serialized_scalar(value)
            if _is_json_safe(value):
                data[key] = value
        return data

    if dataclasses.is_dataclass(obj):
        for field in dataclasses.fields(obj):
            if field.name in _SKIP_FIELDS:
                continue
            if any(field.name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
                continue
            value = getattr(obj, field.name, None)
            serialized = _serialize_value(value)
            if serialized is not None:
                data[field.name] = serialized
        return data

    try:
        valid_params = set(inspect.signature(obj.__class__.__init__).parameters) - {"self"}
    except (TypeError, ValueError):
        return None

    for key in valid_params:
        if any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
            continue
        if not hasattr(obj, key):
            continue
        serialized = _serialize_value(getattr(obj, key))
        if serialized is not None:
            data[key] = serialized

    return data


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        serialized_list = [_serialize_value(item) for item in value]
        return serialized_list if _is_json_safe(serialized_list) else None
    if isinstance(value, dict):
        serialized_dict = {key: _serialize_value(item) for key, item in value.items()}
        return serialized_dict if _is_json_safe(serialized_dict) else None
    if isinstance(value, BaseDb) and hasattr(value, "to_dict"):
        return value.to_dict()
    serialized_object = _serialize_object(value)
    if serialized_object is not None and _is_json_safe(serialized_object):
        return serialized_object
    return None


def to_dict(knowledge: "Knowledge") -> Dict[str, Any]:
    config: Dict[str, Any] = {"class_path": _class_path(knowledge)}

    for field in dataclasses.fields(knowledge):  # type: ignore[arg-type]
        if field.name in _SKIP_FIELDS:
            continue
        if any(field.name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
            continue
        value = getattr(knowledge, field.name, None)
        serialized = _serialize_value(value)
        if serialized is not None:
            config[field.name] = serialized

    return config


def _import_class(class_path: str) -> Optional[Type[Any]]:
    module_path, class_name = class_path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        log_warning(f"Could not import {class_path}: {str(e)}")
        return None


def _filter_constructor_kwargs(target_cls: Type[Any], data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signature = inspect.signature(target_cls.__init__)
    except (TypeError, ValueError):
        return data
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return data
    valid_params = set(signature.parameters) - {"self"}
    return {key: value for key, value in data.items() if key in valid_params}


def _deserialize_object(data: Dict[str, Any], *, registry: Optional["Registry"] = None) -> Any:
    data = data.copy()
    class_path = data.pop("class_path", None)
    if not class_path:
        return data

    target_cls = _import_class(class_path)
    if target_cls is None:
        return None

    kwargs = {key: _deserialize_value(value, registry=registry) for key, value in data.items()}
    kwargs = _filter_constructor_kwargs(target_cls, kwargs)

    try:
        return target_cls(**kwargs)
    except Exception as e:
        log_warning(f"Could not instantiate {class_path}: {str(e)}")
        return None


def _resolve_vector_db(data: Dict[str, Any], registry: Optional["Registry"] = None) -> Any:
    vector_db_id = data.get("id")
    if registry is not None and vector_db_id:
        vector_db = registry.get_vector_db(vector_db_id)
        if vector_db is not None:
            return vector_db
    return _deserialize_object(data, registry=registry)


def _deserialize_value(value: Any, *, registry: Optional["Registry"] = None) -> Any:
    if isinstance(value, list):
        return [_deserialize_value(item, registry=registry) for item in value]
    if isinstance(value, dict):
        class_path = value.get("class_path")
        if class_path is None:
            return {key: _deserialize_value(item, registry=registry) for key, item in value.items()}
        return _deserialize_object(value, registry=registry)
    return value


def from_dict(cls: Type["Knowledge"], data: Dict[str, Any], registry: Optional["Registry"] = None) -> "Knowledge":
    config = data.copy()
    class_path = config.pop("class_path", None)

    target_cls: Type["Knowledge"] = cls
    if class_path:
        imported_cls = _import_class(class_path)
        if imported_cls is not None:
            target_cls = imported_cls

    if "vector_db" in config and isinstance(config["vector_db"], dict):
        vector_db = _resolve_vector_db(config["vector_db"], registry=registry)
        if vector_db is not None:
            config["vector_db"] = vector_db
        else:
            del config["vector_db"]

    if "contents_db" in config and isinstance(config["contents_db"], dict):
        contents_db = resolve_db_from_config(config["contents_db"], registry=registry)
        if contents_db is not None:
            config["contents_db"] = contents_db
        else:
            del config["contents_db"]

    if "readers" in config and isinstance(config["readers"], dict):
        readers = {
            key: reader
            for key, reader_data in config["readers"].items()
            if isinstance(reader_data, dict)
            and (reader := _deserialize_object(reader_data, registry=registry)) is not None
        }
        if readers:
            config["readers"] = readers
        else:
            del config["readers"]

    if "content_sources" in config and isinstance(config["content_sources"], list):
        content_sources = [
            source
            for source_data in config["content_sources"]
            if isinstance(source_data, dict)
            and (source := _deserialize_object(source_data, registry=registry)) is not None
        ]
        if content_sources:
            config["content_sources"] = content_sources
        else:
            del config["content_sources"]

    config = _filter_constructor_kwargs(target_cls, config)
    return target_cls(**config)


def save(
    knowledge: "Knowledge",
    db: BaseDb,
    *,
    stage: str = "published",
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> Tuple[str, Optional[int]]:
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for save(). Use a sync database.")

    component_id = get_component_id(knowledge)
    display_name = getattr(knowledge, "name", None) or f"{knowledge.__class__.__name__}:{component_id}"

    try:
        db.upsert_component(
            component_id=component_id,
            component_type=ComponentType.KNOWLEDGE,
            name=display_name,
            description=getattr(knowledge, "description", None),
        )
        config = db.upsert_config(
            component_id=component_id,
            config=to_dict(knowledge),
            label=label,
            stage=stage,
            notes=notes,
        )
        return component_id, config.get("version")
    except Exception as e:
        log_error(f"Error saving Knowledge to database: {str(e)}")
        raise


def load(
    cls: Type["Knowledge"],
    component_id: str,
    db: BaseDb,
    *,
    registry: Optional["Registry"] = None,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["Knowledge"]:
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for load(). Use a sync database.")

    data = db.get_config(component_id=component_id, label=label, version=version)
    if data is None:
        return None
    config = data.get("config")
    if config is None:
        return None
    return from_dict(cls, config, registry=registry)


def delete(
    component_id: str,
    db: BaseDb,
    *,
    hard_delete: bool = False,
) -> bool:
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for delete(). Use a sync database.")

    return db.delete_component(component_id=component_id, hard_delete=hard_delete)


def list_knowledge(
    db: BaseDb,
    *,
    include_deleted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    return db.list_components(
        component_type=ComponentType.KNOWLEDGE,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )


def get_knowledge_by_id(
    component_id: str,
    db: BaseDb,
    *,
    registry: Optional["Registry"] = None,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> "Optional[Knowledge]":
    """Load and reconstruct a Knowledge instance from the database by component_id."""
    try:
        row = db.get_config(component_id=component_id, label=label, version=version)
        if row is None:
            return None
        config = row.get("config")
        if config is None:
            return None
        from agno.knowledge.knowledge import Knowledge

        return from_dict(Knowledge, config, registry=registry)
    except Exception as e:
        log_error(f"Error loading Knowledge {component_id} from database: {str(e)}")
        return None
