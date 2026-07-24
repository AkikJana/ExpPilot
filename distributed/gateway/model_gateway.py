"""Model Gateway: the 3-tier routing ladder over agents/llm.py.

This does not replace agents.llm.narrate — it wraps it. Tier 2 (CLOUD) *is*
agents.llm's existing Groq/Anthropic path, numeric guard and all; Tier 0
(TEMPLATE) *is* the caller-supplied deterministic fallback the rest of this
codebase already relies on. What's new is Tier 1 (LOCAL): a local Gemma served
through Ollama, which is the realistic way to run Gemma on a laptop with no CUDA
GPU — the honest dev-machine stand-in for the vLLM/Triton fleet described in
docs/distributed-architecture.md §3. Swapping the LOCAL backend for a real vLLM
gateway call is a one-function change behind this same interface.

Escalation only ever falls DOWN the ladder (cloud -> local -> template) on
failure or absence, never up — and a PII-tagged request never rises past LOCAL
regardless of what the caller asked for, enforced in tiers.GenerationPolicy.
"""
from __future__ import annotations

import os

from agents.llm import verify_numbers
from distributed.gateway.tiers import GenerationPolicy, Tier

_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:9b")
_OLLAMA_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "10"))

_ollama_probed: bool | None = None  # cached availability probe, see _local_available()


def _local_available() -> bool:
    """Cheap, cached reachability probe for the local Ollama server.

    Cached for the process lifetime: an unreachable Ollama server should not cost
    a network timeout on every single generation call.
    """
    global _ollama_probed
    if _ollama_probed is not None:
        return _ollama_probed
    try:
        import requests

        response = requests.get(f"{_OLLAMA_HOST}/api/tags", timeout=2)
        _ollama_probed = response.status_code == 200
    except Exception:
        _ollama_probed = False
    return _ollama_probed


def _local_generate(system: str, prompt: str) -> str | None:
    """Call the local Ollama server. Returns None on any failure — the caller falls
    back down the ladder rather than raising."""
    if not _local_available():
        return None
    try:
        import requests

        response = requests.post(
            f"{_OLLAMA_HOST}/api/generate",
            json={
                "model": _OLLAMA_MODEL,
                "prompt": f"{system}\n\n{prompt}",
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 400},
            },
            timeout=_OLLAMA_TIMEOUT_S,
        )
        if response.status_code != 200:
            return None
        text = response.json().get("response", "").strip()
        return text or None
    except Exception:
        return None


def local_available() -> bool:
    """Public probe, for callers/UIs that want to show which tier is actually live."""
    return _local_available()


def active_tiers() -> dict[str, bool]:
    """Which tiers are actually usable right now, for observability/UI display."""
    from agents.llm import llm_available

    return {
        "template": True,  # always available, by construction
        "local": _local_available(),
        "cloud": llm_available(),
    }


def generate(
    system: str,
    prompt: str,
    allowed_values: list[float],
    fallback: str,
    policy: GenerationPolicy,
) -> tuple[str, str, Tier]:
    """Route one generation call down the ladder from policy.effective_max_tier().

    Returns (text, source, tier_used). `source` mirrors agents.llm.narrate's
    vocabulary ('llm', 'llm_rejected_fallback', 'template') with a 'local' source
    added for Tier 1. Every returned text has already passed the numeric guard, or
    is the deterministic fallback — the gateway never hands back ungrounded prose.
    """
    ceiling = policy.effective_max_tier()

    if ceiling >= Tier.CLOUD:
        from agents.llm import narrate

        text, source = narrate(system=system, prompt=prompt, allowed_values=allowed_values, fallback=fallback)
        if source == "llm":
            return text, source, Tier.CLOUD
        # narrate() already fell back to `fallback` internally (template) on failure
        # or a rejected numeric guard; try LOCAL before accepting that fallback,
        # since a local model may succeed where the cloud provider is absent.

    if ceiling >= Tier.LOCAL:
        local_text = _local_generate(system, prompt)
        if local_text is not None:
            if verify_numbers(local_text, allowed_values):
                return local_text, "local", Tier.LOCAL
            # Local model produced an ungrounded number — same rejection rule as
            # Tier 2, fall through to the template.

    return fallback, "template", Tier.TEMPLATE
