"""Switchable scenario-generation backends.

Preference chain (resolved in factory): local Gemma  ->  API model  ->  template.
All LLM backends fall back to the deterministic TemplateBackend on any failure, so
generation never hard-fails and always yields a valid, simulator-ready ScenarioSpec.
"""
from __future__ import annotations

import json
import re

from synthgen.capabilities import Capabilities
from synthgen.config import SynthGenConfig
from synthgen.spec import Guardrail, ScenarioRequest, ScenarioSpec, Segment

_SYSTEM = (
    "You are a senior experimentation data scientist. Given a business goal, design a "
    "realistic A/B experiment scenario for a telecom consumer app. Respond with ONLY a "
    "JSON object matching this schema (no prose):\n"
    "{name, hypothesis, metric_type ('binary'|'continuous'), base_rate (0-1), "
    "true_effect (absolute), daily_traffic (int), n_days (int), "
    "segments:[{name, weight, effect_multiplier, base_shift}], "
    "guardrail:{name, base_rate, treatment_delta}, covariate_correlation (0-1)}."
)

# Category -> (base_rate, typical absolute effect). Mirrors the ExpPilot registry.
_CATEGORY_PRIORS = {
    "checkout": (0.12, 0.02),
    "device_bundles": (0.08, 0.018),
    "plan_upgrades": (0.06, 0.017),
    "churn": (0.20, 0.03),
    "onboarding": (0.30, 0.025),
    "payments": (0.15, 0.02),
}


def _infer_category(goal: str) -> str:
    try:
        from agents.rag import infer_category

        return infer_category(goal)
    except Exception:
        return "checkout"


class TemplateBackend:
    """Deterministic, dependency-free scenario generator (always available)."""

    name = "template"

    def propose(self, request: ScenarioRequest, config: SynthGenConfig) -> ScenarioSpec:
        cat = _infer_category(request.goal)
        base_rate, effect = _CATEGORY_PRIORS.get(cat, (0.10, 0.02))
        seed = request.seed if request.seed is not None else config.seed
        # small deterministic jitter from the seed so repeated goals vary a little
        jitter = ((seed % 7) - 3) * 0.001
        return ScenarioSpec(
            name=f"{cat}_experiment",
            hypothesis=f"The proposed change for '{request.goal}' increases the primary metric.",
            metric_type=request.hint_metric,
            base_rate=round(min(max(base_rate + jitter, 0.01), 0.95), 4),
            true_effect=round(effect, 4),
            segments=[
                Segment(name=f"{cat}_core", weight=0.6, effect_multiplier=1.2),
                Segment(name=f"{cat}_casual", weight=0.4, effect_multiplier=0.6, base_shift=-0.02),
            ],
            guardrail=Guardrail(name="latency_breach_rate", base_rate=0.05, treatment_delta=0.0),
            n_days=14,
            daily_traffic=6000,
            covariate_correlation=0.5,
        )


class _LLMBackend:
    """Base for LLM backends: prompt -> raw JSON -> validated spec (template on failure)."""

    name = "llm"

    def _complete(self, system: str, user: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def propose(self, request: ScenarioRequest, config: SynthGenConfig) -> ScenarioSpec:
        fallback = TemplateBackend().propose(request, config)
        try:
            raw = self._complete(_SYSTEM, f"Business goal: {request.goal}")
            data = _extract_json(raw)
            if not data:
                return fallback
            # merge onto the template so missing fields stay valid
            merged = fallback.model_dump()
            for k in (
                "name", "hypothesis", "metric_type", "base_rate", "true_effect",
                "daily_traffic", "n_days", "covariate_correlation",
            ):
                if k in data and data[k] is not None:
                    merged[k] = data[k]
            if isinstance(data.get("segments"), list) and data["segments"]:
                merged["segments"] = data["segments"]
            if isinstance(data.get("guardrail"), dict):
                merged["guardrail"] = {**merged["guardrail"], **data["guardrail"]}
            return ScenarioSpec(**merged)
        except Exception:
            return fallback


class ApiBackend(_LLMBackend):
    """Cloud/API model via LangChain (Groq or Anthropic, per env keys) or LiteLLM."""

    name = "api"

    def _complete(self, system: str, user: str) -> str:
        # Prefer the already-wired LangChain model (Groq/Anthropic) from agents.llm.
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            from agents.llm import _build_model

            model = _build_model()
            if model is not None:
                resp = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
                return resp.content if isinstance(resp.content, str) else str(resp.content)
        except Exception:
            pass
        # Optional: provider-agnostic LiteLLM path.
        import os

        import litellm  # type: ignore

        resp = litellm.completion(
            model=os.getenv("SYNTHGEN_API_MODEL", "groq/llama-3.3-70b-versatile"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp["choices"][0]["message"]["content"]


class LocalGemmaBackend(_LLMBackend):
    """Local Gemma 3n E4B via a Hugging Face text-generation pipeline.

    For maximally optimized on-device inference, this same model can be served
    through Google's LiteRT / AI Edge runtime; the HF pipeline is the portable
    OSS path used here. The pipeline is lazily constructed and cached.
    """

    name = "local"

    def __init__(self, config: SynthGenConfig):
        self._config = config
        self._pipe = None

    def _pipeline(self):
        if self._pipe is None:
            from transformers import pipeline  # heavy import, done lazily

            device = 0 if self._config.device in ("auto", "cuda") else -1
            self._pipe = pipeline(
                "text-generation",
                model=self._config.local_model,
                device_map="auto" if device == 0 else None,
            )
        return self._pipe

    def _complete(self, system: str, user: str) -> str:
        pipe = self._pipeline()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        out = pipe(messages, max_new_tokens=512, do_sample=False)
        text = out[0]["generated_text"]
        # transformers returns the chat turns; grab the assistant content.
        if isinstance(text, list):
            return text[-1].get("content", "")
        return str(text)


def _extract_json(raw: str) -> dict | None:
    """Pull the first JSON object out of an LLM response."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def get_backend(config: SynthGenConfig, caps: Capabilities):
    """Resolve the configured provider preference against detected capabilities."""
    import os

    has_api_key = bool(os.getenv("GROQ_API_KEY") or os.getenv("ANTHROPIC_API_KEY")) or caps.litellm

    pref = config.provider
    if pref == "template":
        return TemplateBackend()
    if pref == "local":
        return LocalGemmaBackend(config) if caps.local_llm_ready else (
            ApiBackend() if has_api_key else TemplateBackend()
        )
    if pref == "api":
        return ApiBackend() if has_api_key else TemplateBackend()
    # auto
    if caps.local_llm_ready:
        return LocalGemmaBackend(config)
    if has_api_key:
        return ApiBackend()
    return TemplateBackend()
