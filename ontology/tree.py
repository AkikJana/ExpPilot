"""A small, serializable hypothesis tree with deterministic pruning rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import uuid4


NodeStatus = Literal["proposed", "active", "validated", "invalidated", "queued"]


@dataclass
class HypothesisNode:
    statement: str
    rationale: str
    segment: str
    parent_id: str | None = None
    id: str = field(default_factory=lambda: f"hyp_{uuid4().hex[:10]}")
    status: NodeStatus = "proposed"
    children: list["HypothesisNode"] = field(default_factory=list)

    def branch(self, statement: str, rationale: str, segment: str) -> "HypothesisNode":
        child = HypothesisNode(statement, rationale, segment, parent_id=self.id)
        self.children.append(child)
        return child

    def invalidate(self) -> None:
        self.status = "invalidated"
        for child in self.children:
            child.invalidate()

    def as_dict(self) -> dict:
        value = asdict(self)
        value["children"] = [child.as_dict() for child in self.children]
        return value


def initial_tree(goal: str, candidates: list[dict]) -> HypothesisNode:
    root = HypothesisNode(
        statement=goal,
        rationale="Root product objective; children must be independently testable.",
        segment="all_users",
        status="active",
    )
    for candidate in candidates:
        root.branch(
            candidate["statement"], candidate.get("rationale", ""), candidate.get("segment", "all_users")
        )
    return root
