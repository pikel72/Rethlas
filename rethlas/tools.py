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

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _TOOL_DESCRIPTIONS.get(name, f"Rethlas tool {name}"),
                    "parameters": _TOOL_PARAMETERS.get(name, {"type": "object", "properties": {}}),
                },
            }
            for name in self.names
        ]


def build_generation_tool_registry(config: RethlasConfig) -> ToolRegistry:
    module = _load_module(config.paths.generation_dir / "mcp" / "server.py", "rethlas_generation_mcp_tools")
    tool_names = [
        "search_arxiv_theorems",
        "search_math_results",
        "fetch_math_source",
        "read_run_context",
        "list_problem_references",
        "read_problem_reference",
        "verify_proof_service",
        "memory_init",
        "memory_append",
        "memory_search",
        "record_math_note",
        "search_memory",
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


_TOOL_DESCRIPTIONS = {
    "memory_init": "Initialize append-only memory channels for a problem.",
    "memory_append": "Append a structured record to a problem memory channel.",
    "memory_search": "Search prior structured memory for a problem.",
    "record_math_note": "Record a schema-guided mathematical note for a proof run.",
    "search_memory": "Search compact mathematical memory notes for a proof run.",
    "branch_update": "Record current branch state for a proof strategy.",
    "search_arxiv_theorems": "Search theorem-like statements from arXiv-related sources.",
    "search_math_results": "Search for mathematical results with normalized provenance and purpose.",
    "fetch_math_source": "Fetch or read cached mathematical source context for a result.",
    "read_run_context": "Read a bounded snapshot of the current proof run context for a problem.",
    "list_problem_references": "List user-provided reference files for the current problem.",
    "read_problem_reference": "Read a bounded text excerpt from a user-provided problem reference file.",
    "verify_proof_service": "Ask the verification service to check a complete proof.",
}


_TOOL_PARAMETERS: dict[str, dict[str, Any]] = {
    "memory_init": {
        "type": "object",
        "required": ["problem_id"],
        "properties": {
            "problem_id": {"type": "string"},
            "meta": {"type": "object"},
        },
    },
    "memory_append": {
        "type": "object",
        "required": ["problem_id", "channel", "record"],
        "properties": {
            "problem_id": {"type": "string"},
            "channel": {"type": "string"},
            "record": {"type": "object"},
        },
    },
    "memory_search": {
        "type": "object",
        "required": ["problem_id", "query"],
        "properties": {
            "problem_id": {"type": "string"},
            "query": {"type": "string"},
            "channels": {"type": "array", "items": {"type": "string"}},
            "limit_per_channel": {"type": "integer"},
        },
    },
    "branch_update": {
        "type": "object",
        "required": ["problem_id", "branch_id", "state"],
        "properties": {
            "problem_id": {"type": "string"},
            "branch_id": {"type": "string"},
            "state": {"type": "object"},
        },
    },
    "search_arxiv_theorems": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "num_results": {"type": "integer"},
        },
    },
    "search_math_results": {
        "type": "object",
        "required": ["problem_id", "query"],
        "properties": {
            "problem_id": {"type": "string"},
            "query": {"type": "string"},
            "purpose": {
                "type": "string",
                "enum": ["background", "lemma", "counterexample", "definition", "repair"],
            },
            "num_results": {"type": "integer"},
        },
    },
    "fetch_math_source": {
        "type": "object",
        "required": ["problem_id", "source_id", "focus_query"],
        "properties": {
            "problem_id": {"type": "string"},
            "source_id": {"type": "string"},
            "focus_query": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
    },
    "read_run_context": {
        "type": "object",
        "required": ["problem_id"],
        "properties": {
            "problem_id": {"type": "string"},
            "include_draft": {"type": "boolean"},
            "include_recent_events": {"type": "boolean"},
            "max_chars": {"type": "integer"},
        },
    },
    "list_problem_references": {
        "type": "object",
        "required": ["problem_id"],
        "properties": {
            "problem_id": {"type": "string"},
        },
    },
    "read_problem_reference": {
        "type": "object",
        "required": ["problem_id", "relative_path"],
        "properties": {
            "problem_id": {"type": "string"},
            "relative_path": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
    },
    "verify_proof_service": {
        "type": "object",
        "required": ["statement", "proof"],
        "properties": {
            "statement": {"type": "string"},
            "proof": {"type": "string"},
        },
    },
    "record_math_note": {
        "type": "object",
        "required": ["problem_id", "note_type", "content"],
        "properties": {
            "problem_id": {"type": "string"},
            "note_type": {
                "type": "string",
                "enum": [
                    "conclusion",
                    "source_note",
                    "subgoal",
                    "proof_step",
                    "failed_path",
                    "decision",
                    "verification_report",
                ],
            },
            "content": {"type": "object"},
            "branch_id": {"type": "string"},
        },
    },
    "search_memory": {
        "type": "object",
        "required": ["problem_id", "query"],
        "properties": {
            "problem_id": {"type": "string"},
            "query": {"type": "string"},
            "note_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "conclusion",
                        "source_note",
                        "subgoal",
                        "proof_step",
                        "failed_path",
                        "decision",
                        "verification_report",
                    ],
                },
            },
            "limit": {"type": "integer"},
        },
    },
}
