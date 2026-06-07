from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .config import RethlasConfig


@dataclass(frozen=True)
class SubAgentTask:
    task_id: str
    prompt: str
    depth: int = 1


@dataclass(frozen=True)
class SubAgentResult:
    task_id: str
    ok: bool
    summary: str
    depth: int


class SubAgentRunner:
    def __init__(self, config: RethlasConfig):
        self.config = config

    def run_mock_batch(self, tasks: Iterable[SubAgentTask]) -> List[SubAgentResult]:
        task_list = list(tasks)
        if len(task_list) > self.config.agents.max_threads:
            raise ValueError(
                f"Sub-agent batch has {len(task_list)} tasks, "
                f"but max_threads={self.config.agents.max_threads}"
            )
        results: List[SubAgentResult] = []
        for task in task_list:
            if task.depth > self.config.agents.max_depth:
                results.append(
                    SubAgentResult(
                        task_id=task.task_id,
                        ok=False,
                        summary=f"Rejected: depth {task.depth} exceeds max_depth={self.config.agents.max_depth}",
                        depth=task.depth,
                    )
                )
                continue
            results.append(
                SubAgentResult(
                    task_id=task.task_id,
                    ok=True,
                    summary=f"Mock sub-agent completed task: {task.prompt}",
                    depth=task.depth,
                )
            )
        return results
