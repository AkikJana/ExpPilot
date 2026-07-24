"""Tier and policy types for the model gateway routing ladder.

docs/distributed-architecture.md §3:
  TIER 0  deterministic template      always available, no model
  TIER 1  Gemma + LoRA on vLLM        high-volume, low-latency, PII-safe (on-prem)
  TIER 2  frontier cloud API          low-volume, high-stakes generation
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class Tier(IntEnum):
    """Ordered so `Tier.CLOUD > Tier.LOCAL > Tier.TEMPLATE` — comparisons express
    "how far up the ladder" without a lookup table."""

    TEMPLATE = 0
    LOCAL = 1
    CLOUD = 2


class TaskClass(str, Enum):
    """What kind of generation this is — used only for observability labeling, not
    for routing itself (routing is the caller's explicit GenerationPolicy.max_tier)."""

    NARRATION = "narration"
    HYPOTHESIS_DESIGN = "hypothesis_design"
    RULE_EXPLANATION = "rule_explanation"
    CHAT = "chat"
    RECOMMENDATION = "recommendation"


@dataclass(frozen=True)
class GenerationPolicy:
    """Caller-declared intent for one generation call.

    `pii` is the hard constraint: a PII-tagged request can never route above
    Tier.LOCAL, regardless of what max_tier asks for — consumer text never leaves
    hardware we own (§3, §8). `max_tier` is the caller's design-time preference
    (e.g. hypothesis design wants CLOUD's reasoning quality; routine narration is
    fine at LOCAL) and the ladder falls back *down* from there on failure, never up.
    """

    task_class: TaskClass
    pii: bool = False
    max_tier: Tier = Tier.CLOUD

    def effective_max_tier(self) -> Tier:
        """The real ceiling once the PII constraint is applied."""
        return Tier.LOCAL if self.pii else self.max_tier
