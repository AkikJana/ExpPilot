"""Model gateway routing tests. No real network calls: Ollama and cloud providers
are monkeypatched, so these pass with no Ollama server and no API key present —
exactly the environment this whole codebase is built to run keyless in."""
from __future__ import annotations

import distributed.gateway.model_gateway as gw
from distributed.gateway.tiers import GenerationPolicy, TaskClass, Tier

FALLBACK = "Day 6: lift +2.10pp. P(beats control) = 91.0%."
ALLOWED = [2.1, 91.0]


def _reset_ollama_cache():
    gw._ollama_probed = None


def test_template_tier_needs_zero_network_calls(monkeypatch):
    """With max_tier=TEMPLATE, neither Ollama nor a cloud provider should be touched."""
    _reset_ollama_cache()

    def _boom(*args, **kwargs):
        raise AssertionError("network call attempted at TEMPLATE tier")

    monkeypatch.setattr("requests.get", _boom)
    monkeypatch.setattr("requests.post", _boom)

    policy = GenerationPolicy(task_class=TaskClass.NARRATION, max_tier=Tier.TEMPLATE)
    text, source, tier = gw.generate("system", "prompt", ALLOWED, FALLBACK, policy)

    assert text == FALLBACK
    assert source == "template"
    assert tier == Tier.TEMPLATE


def test_pii_pins_ceiling_to_local_even_when_cloud_requested(monkeypatch):
    """A PII-tagged request asking for Tier.CLOUD must never reach the cloud path —
    proven by making the cloud call *succeed* and asserting it's still not used."""
    _reset_ollama_cache()
    monkeypatch.setattr(gw, "_local_available", lambda: False)

    def _cloud_would_succeed(**kwargs):
        return "This would be a perfectly valid cloud response.", "llm"

    import agents.llm

    monkeypatch.setattr(agents.llm, "narrate", _cloud_would_succeed)

    policy = GenerationPolicy(task_class=TaskClass.CHAT, pii=True, max_tier=Tier.CLOUD)
    text, source, tier = gw.generate("system", "prompt", ALLOWED, FALLBACK, policy)

    assert tier != Tier.CLOUD, "PII request must never route to the cloud tier"
    assert tier == Tier.TEMPLATE  # local also unavailable in this test, so falls all the way
    assert text == FALLBACK


def test_falls_back_from_local_to_template_when_ollama_unreachable(monkeypatch):
    _reset_ollama_cache()
    monkeypatch.setattr(gw, "_local_available", lambda: False)

    policy = GenerationPolicy(task_class=TaskClass.NARRATION, max_tier=Tier.LOCAL)
    text, source, tier = gw.generate("system", "prompt", ALLOWED, FALLBACK, policy)

    assert tier == Tier.TEMPLATE
    assert source == "template"
    assert text == FALLBACK


def test_local_tier_used_when_ollama_available_and_numbers_verify(monkeypatch):
    _reset_ollama_cache()
    monkeypatch.setattr(gw, "_local_available", lambda: True)
    monkeypatch.setattr(gw, "_local_generate", lambda system, prompt: "Lift is +2.10pp, 91.0% confident.")

    policy = GenerationPolicy(task_class=TaskClass.NARRATION, max_tier=Tier.LOCAL)
    text, source, tier = gw.generate("system", "prompt", ALLOWED, FALLBACK, policy)

    assert tier == Tier.LOCAL
    assert source == "local"
    assert "2.10" in text


def test_local_tier_rejected_on_ungrounded_number_falls_to_template(monkeypatch):
    """The numeric guard applies at Tier 1 exactly as it does at Tier 2 — a local
    model is not exempt from the anti-hallucination check."""
    _reset_ollama_cache()
    monkeypatch.setattr(gw, "_local_available", lambda: True)
    monkeypatch.setattr(
        gw, "_local_generate", lambda system, prompt: "Lift is +47.00pp, which is not in the allowed set."
    )

    policy = GenerationPolicy(task_class=TaskClass.NARRATION, max_tier=Tier.LOCAL)
    text, source, tier = gw.generate("system", "prompt", ALLOWED, FALLBACK, policy)

    assert tier == Tier.TEMPLATE
    assert source == "template"
    assert text == FALLBACK


def test_active_tiers_reports_template_always_true(monkeypatch):
    monkeypatch.setattr(gw, "_local_available", lambda: False)
    status = gw.active_tiers()
    assert status["template"] is True
    assert status["local"] is False


def test_local_availability_probe_is_cached(monkeypatch):
    """The reachability probe must not fire a network call on every generation —
    only once per process (cache), so an unreachable Ollama doesn't add latency
    to every single request."""
    _reset_ollama_cache()
    call_count = {"n": 0}

    def _counting_get(*args, **kwargs):
        call_count["n"] += 1
        raise ConnectionError("simulated unreachable")

    monkeypatch.setattr("requests.get", _counting_get)

    assert gw._local_available() is False
    assert gw._local_available() is False
    assert gw._local_available() is False
    assert call_count["n"] == 1
