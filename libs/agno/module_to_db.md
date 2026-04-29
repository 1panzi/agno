# 模块持久化改造参考文档

基于 `agno/models/` 模块的改造，总结出一套统一的"委托模式"，用于将任意组件（Agent、Team、Model 等）接入数据库持久化。

---

## 各模块持久化状态

| 模块 | 路径 | 有 `_storage.py` | 有 `save/load` | 状态 |
|---|---|:---:|:---:|---|
| **Agent** | `agno/agent/` | ✅ | ✅ | 完整实现 |
| **Team** | `agno/team/` | ✅ | ✅ | 完整实现 |
| **Workflow** | `agno/workflow/` | ❌ | ✅ | `save/load` 直接写在 `workflow.py`，未抽到 `_storage.py` |
| **Model** | `agno/models/` | ✅ | ✅ | 完整实现（本次新增） |
| **Knowledge** | `agno/knowledge/` | ❌ | ❌ | 完全没有 DB 持久化 |
| **Memory** | `agno/memory/` | ❌ | ❌ | 完全没有 DB 持久化 |
| **LearningMachine** | `agno/learn/` | ❌ | 仅 `to_dict/from_dict` | 有序列化但无 DB 存取 |
| **VectorDB** | `agno/vectordb/` | ❌ | ❌ | 各 provider 自管连接，不需要 component 持久化 |
| **Tools** | `agno/tools/` | ❌ | ❌ | 无状态，不需要持久化 |
| **Eval** | `agno/eval/` | ❌ | ❌ | 评估结果目前无持久化 |

### 待改造优先级

1. **`knowledge`** — 知识库配置（loader、embedder、vectordb 指向）需要能保存/恢复
2. **`memory`** — MemoryManager 配置需要能跟随 Agent 一起序列化
3. **`learn`** — 已有 `to_dict/from_dict`，只差 `save/load` 到 DB 的那一层
4. **`workflow`** — 已有 `save/load` 但逻辑直接堆在 `workflow.py`，建议抽出到 `_storage.py` 对齐架构

---

## AgentOS 关联改动

每新增一个组件类型，除核心持久化三步外，还需要同步修改以下位置：

### 已完成（Model 为例）

| 文件 | 改动内容 |
|---|---|
| `agno/db/base.py` | `ComponentType` 枚举加 `MODEL = "model"` |
| `agno/os/schema.py` | `ComponentType` 枚举加 `MODEL = "model"`，使 `POST /components` API 接受 `"model"` 类型 |

### 说明

`agno/os/routers/components/components.py` 的 `POST /components` 路由是**通用的**，底层 `db.create_component_with_config()` 不限制类型。唯一的拦截点是 `os/schema.py` 的 `ComponentType` 枚举做 FastAPI 入参校验，因此每新增一个类型只需在这两处枚举同步添加即可，不需要新建路由。

---

## 改造步骤

### 第一步：`agno/db/base.py` — 注册组件类型

在 `ComponentType` 枚举中新增对应的组件类型：

```python
class ComponentType(str, Enum):
    AGENT = "agent"
    TEAM = "team"
    WORKFLOW = "workflow"
    MODEL = "model"
    # 新增你的组件类型，例如：
    # FOO = "foo"
```

---

### 第二步：`agno/<module>/base.py` — 给基类添加持久化方法

在基类中添加 `to_dict / from_dict / save / load / delete` 方法，全部通过懒加载（在方法体内 import）委托到 `_storage.py`，避免循环依赖。

```python
if TYPE_CHECKING:
    from agno.db.base import BaseDb

def to_dict(self) -> Dict[str, Any]:
    from agno.<module>._storage import to_dict as _to_dict
    return _to_dict(self)

@classmethod
def from_dict(cls, data: Dict[str, Any]) -> "MyClass":
    from agno.<module>._storage import from_dict as _from_dict
    return _from_dict(cls, data)

def save(
    self,
    db: "BaseDb",
    *,
    stage: str = "published",
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> Tuple[str, Optional[int]]:
    from agno.<module>._storage import save as _save
    return _save(self, db, stage=stage, label=label, notes=notes)

@classmethod
def load(
    cls,
    component_id: str,
    db: "BaseDb",
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["MyClass"]:
    from agno.<module>._storage import load as _load
    return _load(cls, component_id, db, label=label, version=version)

def delete(
    self,
    component_id: str,
    db: "BaseDb",
    *,
    hard_delete: bool = False,
) -> bool:
    from agno.<module>._storage import delete as _delete
    return _delete(component_id, db, hard_delete=hard_delete)
```

---

### 第三步：`agno/<module>/_storage.py` — 新建持久化逻辑模块

新建独立的 `_storage.py`，实现所有序列化/反序列化/DB 操作，不在基类中写具体逻辑。

#### 文件结构模板

```python
"""Database persistence helpers for <Module>."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type
from uuid import uuid4

if TYPE_CHECKING:
    from agno.<module>.base import MyClass

from agno.db.base import BaseDb, ComponentType
from agno.utils.log import log_error, log_warning

# 运行时状态字段，不序列化
_SKIP_FIELDS = frozenset({
    "field_a",
    "field_b",
})

# 敏感凭证字段后缀，不序列化
_SENSITIVE_SUFFIXES = ("_key", "_secret", "_token", "_password", "_credential")


def get_component_id(obj: "MyClass") -> str:
    uid = str(uuid4())[:8]
    return f"<module>:{obj.__class__.__name__}:{obj.id}:{uid}"


def to_dict(obj: "MyClass") -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    config["class_path"] = f"{obj.__class__.__module__}.{obj.__class__.__name__}"

    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if f.name in _SKIP_FIELDS:
            continue
        if any(f.name.endswith(s) for s in _SENSITIVE_SUFFIXES):
            continue
        val = getattr(obj, f.name, None)
        if val is None:
            continue
        if isinstance(val, (str, int, float, bool)):
            config[f.name] = val
        elif isinstance(val, (list, dict)):
            try:
                import json
                json.dumps(val)
                config[f.name] = val
            except (TypeError, ValueError):
                pass

    return config


def from_dict(cls: Type["MyClass"], data: Dict[str, Any]) -> "MyClass":
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
    obj: "MyClass",
    db: BaseDb,
    *,
    stage: str = "published",
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> Tuple[str, Optional[int]]:
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for save(). Use a sync database.")

    component_id = get_component_id(obj)

    try:
        db.upsert_component(
            component_id=component_id,
            component_type=ComponentType.MODEL,  # 替换为对应的 ComponentType
            name=getattr(obj, "name", None) or f"{obj.__class__.__name__}:{obj.id}",
        )
        config = db.upsert_config(
            component_id=component_id,
            config=to_dict(obj),
            label=label,
            stage=stage,
            notes=notes,
        )
        return component_id, config.get("version")
    except Exception as e:
        log_error(f"Error saving to database: {str(e)}")
        raise


def load(
    cls: Type["MyClass"],
    component_id: str,
    db: BaseDb,
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["MyClass"]:
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
    if not isinstance(db, BaseDb):
        raise ValueError("Async databases not yet supported for delete(). Use a sync database.")

    return db.delete_component(component_id=component_id, hard_delete=hard_delete)


def list_components(
    db: BaseDb,
    *,
    include_deleted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    return db.list_components(
        component_type=ComponentType.MODEL,  # 替换为对应的 ComponentType
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )

```

---

### 第四步：`agno/os/schema.py` — 注册 AgentOS API 类型

在 `os/schema.py` 的 `ComponentType` 枚举中同步添加，使 `POST /components` API 能接受该类型：

```python
class ComponentType(str, Enum):
    AGENT = "agent"
    TEAM = "team"
    WORKFLOW = "workflow"
    MODEL = "model"
    # FOO = "foo"
```

---

### 第五步：`agno/<module>/_storage.py` 和 `base.py` — 暴露顶层查询函数

在 `_storage.py` 末尾实现 `get_xxx_by_id`，供 AgentOS router 调用：

```python
def get_xxx_by_id(
    component_id: str,
    db: BaseDb,
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["MyClass"]:
    """Load and reconstruct a component instance from the database by component_id."""
    try:
        row = db.get_config(component_id=component_id, label=label, version=version)
        if row is None:
            return None
        config = row.get("config")
        if config is None:
            return None
        from agno.<module>.base import MyClass
        return from_dict(MyClass, config)
    except Exception as e:
        log_error(f"Error loading {component_id} from database: {str(e)}")
        return None
```

在 `base.py` 模块级别（类定义之外）暴露顶层函数，对标 `get_agent_by_id`：

```python
# agno/<module>/base.py 末尾（类定义之外）
def get_xxx_by_id(
    component_id: str,
    db: "BaseDb",
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["MyClass"]:
    from agno.<module>._storage import get_xxx_by_id as _get
    return _get(component_id, db, label=label, version=version)
```

**用法：**
```python
from agno.<module>.base import get_xxx_by_id

obj = get_xxx_by_id(component_id, db)
```

在 `_storage.py` 实现具体逻辑后，在 `base.py` 模块级别（类定义之外）暴露顶层函数，供 AgentOS router 和外部调用方使用，对标 `get_agent_by_id` 的用法：

```python
# agno/<module>/base.py 末尾（类定义之外）
def get_xxx_by_id(
    component_id: str,
    db: "BaseDb",
    *,
    label: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional["MyClass"]:
    from agno.<module>._storage import get_xxx_by_id as _get
    return _get(component_id, db, label=label, version=version)
```

**用法：**
```python
from agno.<module>.base import get_xxx_by_id

obj = get_xxx_by_id(component_id, db)
```

---

### 第六步：写集成测试（连接真实 DB）

**测试文件路径（相对仓库根）：**

```
cookbook/00_my_test/test_<module>.py
```

参考：`cookbook/00_my_test/test_model.py`（相对仓库根，即 `../../cookbook/00_my_test/test_model.py` 相对于 `libs/agno/`）

**前置条件：**
- 启动 PostgreSQL：`./cookbook/scripts/run_pgvector.sh`
- 使用 `.venvs/demo/bin/python` 运行

#### DB 连接配置

```python
from agno.db.postgres import PostgresDb

DB_URL = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(id="basic-db", db_url=DB_URL)
```

#### 测试函数结构

每个函数测试一个独立场景，函数末尾 `print("... PASS")` 确认通过，最后统一在 `if __name__ == "__main__"` 中调用：

```python
"""Tests for <Module> serialization, save, and load from DB."""

from agno.db.postgres import PostgresDb
from agno.<module>._storage import from_dict, get_component_id, list_components, load, to_dict
from agno.<module>.base import MyClass
from agno.<module>.subclass_a import SubclassA
from agno.<module>.subclass_b import SubclassB

DB_URL = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(id="basic-db", db_url=DB_URL)


def test_from_dict_infers_subclass_a():
    original = SubclassA(id="obj-a", temperature=0.5, max_tokens=512)
    data = to_dict(original)
    restored = from_dict(MyClass, data)

    assert type(restored).__name__ == "SubclassA"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    print(f"test_from_dict_infers_subclass_a PASS (type={type(restored).__name__})")


def test_from_dict_infers_subclass_b():
    original = SubclassB(id="obj-b", temperature=0.3, max_tokens=1024)
    data = to_dict(original)
    restored = from_dict(MyClass, data)

    assert type(restored).__name__ == "SubclassB"
    assert restored.id == original.id
    print(f"test_from_dict_infers_subclass_b PASS (type={type(restored).__name__})")


def test_component_id_uniqueness():
    obj = SubclassA(id="obj-a")
    id1 = get_component_id(obj)
    id2 = get_component_id(obj)

    assert id1.startswith("<module>:SubclassA:obj-a:")
    assert id1 != id2
    print(f"test_component_id_uniqueness PASS ({id1} vs {id2})")


def test_credentials_not_serialized():
    obj = SubclassA(id="obj-a", api_key="sk-should-not-persist", base_url="https://proxy.example.com")
    data = to_dict(obj)

    assert "api_key" not in data
    assert data["base_url"] == "https://proxy.example.com"
    print("test_credentials_not_serialized PASS")


def test_save_returns_component_id_and_version():
    obj = SubclassA(id="obj-a", temperature=0.7, max_tokens=1024)
    component_id, version = obj.save(db, label="save-test")

    assert component_id.startswith("<module>:SubclassA:obj-a:")
    assert version is not None
    print(f"test_save_returns_component_id_and_version PASS (component_id={component_id}, version={version})")


def test_save_and_load():
    original = SubclassA(id="obj-a", temperature=0.3, max_tokens=256)
    component_id, _ = original.save(db, label="load-test")

    restored = load(MyClass, component_id, db, label="load-test")

    assert restored is not None
    assert type(restored).__name__ == "SubclassA"
    assert restored.id == original.id
    assert restored.temperature == original.temperature
    assert restored.max_tokens == original.max_tokens
    print(f"test_save_and_load PASS (component_id={component_id})")


def test_save_and_load_credentials_not_persisted():
    original = SubclassA(id="obj-a", api_key="sk-secret", temperature=0.4)
    component_id, _ = original.save(db, label="cred-test")

    restored = load(MyClass, component_id, db, label="cred-test")

    assert restored is not None
    assert getattr(restored, "api_key", None) != "sk-secret"
    print(f"test_save_and_load_credentials_not_persisted PASS (component_id={component_id})")


def test_list_components():
    SubclassA(id="obj-1", temperature=0.1).save(db)
    SubclassA(id="obj-2", temperature=0.9).save(db)

    components, total = list_components(db, limit=10)

    assert total >= 2
    assert len(components) > 0
    print(f"test_list_components PASS (total={total})")


if __name__ == "__main__":
    test_from_dict_infers_subclass_a()
    test_from_dict_infers_subclass_b()
    test_component_id_uniqueness()
    test_credentials_not_serialized()
    test_save_returns_component_id_and_version()
    test_save_and_load()
    test_save_and_load_credentials_not_persisted()
    test_list_components()
    print("All tests passed.")
```

#### 运行测试

```bash
# 启动数据库（如未运行）
./cookbook/scripts/run_pgvector.sh

# 运行测试（从仓库根执行）
.venvs/demo/bin/python cookbook/00_my_test/test_<module>.py
```

---

## 关键设计原则

- **懒加载 import**：`base.py` 中所有方法体内才 import `_storage`，避免循环依赖。
- **TYPE_CHECKING 隔离**：`BaseDb` 只在 `TYPE_CHECKING` 块中 import，运行时不引入。
- **`class_path` 序列化**：`to_dict` 记录完整模块路径，`from_dict` 用于动态派发到正确子类。
- **敏感字段过滤**：以 `_key / _secret / _token / _password / _credential` 结尾的字段永不持久化。
- **运行时状态过滤**：通过 `_SKIP_FIELDS` 跳过不属于用户配置的字段。
- **同步优先**：所有 DB 操作当前只支持同步 `BaseDb`，异步版本留待后续扩展。


---

## 编辑工具使用经验

### edit 工具偶发失效时的替代方案

在某些会话中 edit 工具会持续报错（old_string / new_string missing），此时用 shell 命令直接操作文件：

**追加内容到文件末尾（推荐用 python3）：**

```bash
python3 -c "
content = open('/path/to/file.py').read()
addition = chr(10) + chr(10) + 'def new_function():' + chr(10) + '    pass'
open('/path/to/file.py', 'w').write(content + addition)
"
```

**注意事项：**
- cat >> heredoc 方式在内容含单引号或反引号时容易出错，优先用 python3 -c 方式
- 追加完成后用 tail -N 验证内容是否正确写入
- 如果需要在文件中间插入，用 python3 读取整个文件、字符串替换后写回

---

## AgentOS Router 开发经验

### 新增模块特有路由的完整流程（以 Model 为例）

**1. 创建路由目录**
```
agno/os/routers/<module>/
  __init__.py   — 导出 get_<module>_router
  schema.py     — 该模块特有的 Pydantic 请求/响应模型
  router.py     — FastAPI 路由实现
```

**2. router.py 签名规范**

统一接收 `os: "AgentOS"`，内部用 `os.db`，与 `get_agent_router` / `get_team_router` 保持一致：

```python
def get_model_router(
    os: "AgentOS",
    settings: AgnoAPISettings = AgnoAPISettings(),
) -> APIRouter:
    if not isinstance(os.db, BaseDb):
        raise ValueError("...")
    db: BaseDb = os.db
```

**3. app.py 注册方式**

```python
# import
from agno.os.routers.models import get_model_router

# 注册（与 agent/team/workflow 一致）
self._add_router(app, get_model_router(self, settings=self.settings))
```

### 接口职责划分原则

- **通用增删改查**（list / get / delete）— 统一用 `/components` 接口，不重复实现：
  - `GET /components?component_type=model` — 列表
  - `GET /components/{id}/configs/current` — 查详情
  - `DELETE /components/{id}` — 删除
- **模块特有操作** — 才在专属 router 中实现，如 `POST /models/{id}/test`



---

## 踩坑记录

### 1. ComponentType 两处枚举必须同步，否则 API 报 422

agno/db/base.py 和 agno/os/schema.py 各有一个 ComponentType 枚举：
- db/base.py 的枚举控制底层 DB 写入
- os/schema.py 的枚举控制 FastAPI 入参校验

只改 db/base.py 不改 os/schema.py，底层 DB 能正常写入，但 POST /components 会被 FastAPI 直接拦截返回 422，排查时容易误判为 DB 问题。两处必须同步添加。

---

### 2. get_component_id() UUID 后缀的设计取舍

Model 的 component_id 格式为 model:ClassName:model_id:uid8，每次调用结果不同。

目的：防碰撞，同一 model.id（如 gpt-4o）多次 save 不互相覆盖，每次产生独立记录。

代价：delete() 不能在内部重新调用 get_component_id()，函数签名设计为接收 component_id 参数，由调用方从 save() 返回值中获取。

---

### 3. shell heredoc 安全写法

cat >> heredoc 单行模式在内容含单引号、反引号、$ 时容易出错。
推荐用 python3 多行模式，定界符用不常见字符串（如 PYEOF）：

  python3 - << 'PYEOF'
  content = open('/path/to/file.py').read()
  content = content.replace('old_string', 'new_string', 1)
  open('/path/to/file.py', 'w').write(content)
  PYEOF

---

### 4. _storage.py 模板只放骨架，不混入具体实现

本次改造中曾把 get_model_by_id 误写进通用 _storage.py 模板，导致类型注解写成了
Optional[Component]（应为 Optional["MyClass"]），且模板里出现了具体模块路径。

规则：文档模板只放函数骨架和占位符，具体实现（如 get_xxx_by_id）单独在第五步说明。
