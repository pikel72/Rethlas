from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from .config import RethlasConfig


ToolFn = Callable[..., Dict[str, Any]]


@dataclass(frozen=True)
class ToolCallResult:
    name: str
    arguments: Dict[str, Any]
    ok: bool
    result: Any = None
    error: str = ""


class ToolRegistry:
    def __init__(self, tools: Mapping[str, ToolFn]):
        self._tools = dict(tools)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        if name not in self._tools:
            return ToolCallResult(
                name=name,
                arguments=dict(arguments),
                ok=False,
                error=f"Unknown tool: {name}",
            )
        try:
            result = self._tools[name](**dict(arguments))
        except Exception as exc:
            return ToolCallResult(
                name=name,
                arguments=dict(arguments),
                ok=False,
                error=str(exc),
            )
        return ToolCallResult(name=name, arguments=dict(arguments), ok=True, result=result)

    def call_json(self, name: str, arguments_json: str) -> ToolCallResult:
        try:
            loaded = json.loads(arguments_json)
        except json.JSONDecodeError as exc:
            return ToolCallResult(name=name, arguments={}, ok=False, error=f"Invalid JSON arguments: {exc}")
        if not isinstance(loaded, dict):
            return ToolCallResult(name=name, arguments={}, ok=False, error="Tool arguments must be a JSON object")
        return self.call(name, loaded)


def build_generation_tool_registry(config: RethlasConfig) -> ToolRegistry:
    module = _load_module(config.paths.generation_dir / "mcp" / "server.py", "rethlas_generation_mcp_tools")
    tool_names = [
        "search_arxiv_theorems",
        "verify_proof_service",
        "memory_init",
        "memory_append",
        "memory_search",
        "branch_update",
    ]
    tools: Dict[str, ToolFn] = {}
    for name in tool_names:
        fn = getattr(module, name)
        tools[name] = fn
    return ToolRegistry(tools)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
