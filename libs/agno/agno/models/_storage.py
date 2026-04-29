"""Database persistence helpers for Model."""

from __future__ import annotations

import dataclasses
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
)
from uuid import uuid4

if TYPE_CHECKING:
    from agno.models.base import Model

from agno.db.base import BaseDb, ComponentType
from agno.utils.log import log_error, log_warning

# Runtime state — not user config, skip serialization
_SKIP_FIELDS = frozenset(
    {
        "model_type",
        "supports_native_structured_outputs",
        "supports_json_schema_outputs",
    }
)

# Field name suffixes that indicate sensitive credentials — never persist
# _SENSITIVE_SUFFIXES = ("_key", "_secret", "_token", "_password", "_credential")
_SENSITIVE_SUFFIXES = ()


def get_component_id(model: "Model") -> str:
    """Generate a human-readable component ID with a UUID suffix to prevent collisions."""
    uid = str(uuid4())[:8]
    return f"model:{model.__class__.__name__}:{model.id}:{uid}"


def to_dict(model: "Model") -> Dict[str, Any]:
    """Serialize all dataclass fields of a Model, skipping only sensitive credentials and runtime state."""
    config: Dict[str, Any] = {}
    config["class_path"] = f"{model.__class__.__module__}.{model.__class__.__name__}"

    for f in dataclasses.fields(model):  # type: ignore[arg-type]
        if f.name in _SKIP_FIELDS:
            continue
        if any(f.name.endswith(s) for s in _SENSITIVE_SUFFIXES):
            continue
        val = getattr(model, f.name, None)
        if val is None:
            continue
        # Serialize any JSON-safe value; skip complex objects (clients, callables)
        if isinstance(val, (str, int, float, bool)):
            config[f.name] = val
        elif isinstance(val, list):
            try:
                import json

                json.dumps(val)
                config[f.name] = val
            except (TypeError, ValueError):
                pass
        elif isinstance(val, dict):
            try:
                import json

                json.dumps(val)
                config[f.name] = val
            except (TypeError, ValueError):
                pass

    return config


def from_dict(cls: Type["Model"], data: Dict[str, Any]) -> "Model":
    """Reconstruct a Model from a serialized dict, using class_path for dynamic dispatch."""
    import importlib
    import inspect

    data = data.copy()
    class_path = data.pop("class_path", None)

    if class_path:
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            mod = importlib.import_module(module_path)
            target_cls = getattr(mod, class_name)
        except (ImportError, AttributeError):
            log_warning(f"Could not import {class_path}, falling back to {cls.__name__}")
            target_cls = cls
    else:
        target_cls = cls

    try:
        valid_params = set(inspect.signature(target_cls.__init__).parameters.keys()) - {"self"}
        filtered = {k: v for k, v in data.items() if k in valid_params}
    except (ValueError, TypeError):
        filtered = data

    return target_cls(**filtered)


def save(
    model: "Model",
    db: BaseDb,
    *,
    model_name: Optional[str] = None,
    stage: str = "published",
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> Tuple[str, Optional[int]]:
    """Save a Model component and config to the database.

    Returns (component_id, version).
    """
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for save(). Use a sync database.")

    component_id = get_component_id(model)
    display_name = model_name or model.name or f"{model.__class__.__name__}:{model.id}"

    try:
        db.upsert_component(
            component_id=component_id,
            component_type=ComponentType.MODEL,
            name=display_name,
        )
        config = db.upsert_config(
            component_id=component_id,
            config=to_dict(model),
            label=label,
            stage=stage,
            notes=notes,
        )
        return component_id, config.get("version")
    except Exception as e:
        log_error(f"Error saving Model to database: {str(e)}")
        raise


def load(
    cls: Type["Model"],
    component_id: str,
    db: BaseDb,
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["Model"]:
    """Load a Model from the database by component_id."""
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for load(). Use a sync database.")

    data = db.get_config(component_id=component_id, label=label, version=version)
    if data is None:
        return None
    config = data.get("config")
    if config is None:
        return None
    return from_dict(cls, config)


def delete(
    component_id: str,
    db: BaseDb,
    *,
    hard_delete: bool = False,
) -> bool:
    """Delete a Model component from the database."""
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for delete(). Use a sync database.")

    return db.delete_component(component_id=component_id, hard_delete=hard_delete)


def list_models(
    db: BaseDb,
    *,
    include_deleted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """List all saved Model components with pagination."""
    return db.list_components(
        component_type=ComponentType.MODEL,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )


def get_model_by_id(
    component_id: str,
    db: BaseDb,
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> "Optional[Model]":
    """Load and reconstruct a Model instance from the database by component_id."""
    from agno.utils.log import log_error

    try:
        row = db.get_config(component_id=component_id, label=label, version=version)
        if row is None:
            return None
        config = row.get("config")
        if config is None:
            return None
        from agno.models.base import Model
        return from_dict(Model, config)
    except Exception as e:
        log_error(f"Error loading Model {component_id} from database: {str(e)}")
        return None
