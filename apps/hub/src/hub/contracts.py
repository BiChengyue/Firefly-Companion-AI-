"""契约校验：加载 contracts/ 下的 JSON Schema 并提供校验入口。

依赖：jsonschema>=4.23（P0 起固定）
"""
import json
import os
from pathlib import Path

import jsonschema

_CONTRACTS = Path(__file__).resolve().parent.parent.parent / "contracts"

_schema_cache: dict[str, dict] = {}


def load_schema(name: str) -> dict:
    """按文件名（不含 .schema.json）加载并缓存 schema。"""
    if name not in _schema_cache:
        path = _CONTRACTS / f"{name}.schema.json"
        with open(path, encoding="utf-8") as f:
            _schema_cache[name] = json.load(f)
    return _schema_cache[name]


def validate(name: str, data: dict) -> list[str]:
    """校验数据；返回错误信息列表（空 = 通过）。不抛异常，便于统一处理。"""
    schema = load_schema(name)
    try:
        jsonschema.validate(data, schema)
        return []
    except jsonschema.ValidationError as e:
        # 只返回一层错误信息（路径 + 消息），不泄露内部
        return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"] if e.message else ["invalid"]
    except Exception as e:  # 非 schema 错误（如类型异常）也收敛为友好信息
        return [f"validation error: {type(e).__name__}"]


def is_valid(name: str, data: dict) -> bool:
    return not validate(name, data)
