"""Tests for editorial_gate after fact-check removal (2026-10-31)."""

import json
import types

import pytest


class _FakeArticle:
    def __init__(self, title="Hadjar joins Red Bull for 2026", content="Isack Hadjar confirmed as Verstappen teammate."):
        self.scraped_title = title
        self.scraped_content = content


# ── Test 1: exactly one generate_caption call, prompt contains key phrases ──

def test_evaluate_article_single_ai_call_with_correct_prompt(monkeypatch):
    calls = []

    def fake_generate_caption(prompt):
        calls.append(prompt)
        return ('{"post_worthy": true, "engagement": "medium", "reason": "ok"}', "model")

    monkeypatch.setattr("app.services.editorial_gate.generate_caption", fake_generate_caption)

    from app.services.editorial_gate import evaluate_article
    evaluate_article(_FakeArticle(), "Formula 1")

    assert len(calls) == 1, f"Expected 1 AI call, got {len(calls)}"
    assert "Do NOT judge whether the facts in the article are true" in calls[0]
    assert "Formula 1" in calls[0]


# ── Test 2: approved reply → correct verdict fields ──

def test_approved_reply_maps_correctly(monkeypatch):
    monkeypatch.setattr(
        "app.services.editorial_gate.generate_caption",
        lambda prompt: ('{"post_worthy": true, "engagement": "high", "reason": "x"}', "m"),
    )
    from app.services.editorial_gate import evaluate_article
    v = evaluate_article(_FakeArticle(), "MotoGP")
    assert v.post_worthy is True
    assert v.engagement == "high"
    assert v.fact_check == "off"
    assert v.fact_check_note == ""


# ── Test 3: rejected reply with invalid engagement → normalised to medium ──

def test_rejected_reply_normalises_engagement(monkeypatch):
    monkeypatch.setattr(
        "app.services.editorial_gate.generate_caption",
        lambda prompt: ('{"post_worthy": false, "engagement": "weird", "reason": "off-niche"}', "m"),
    )
    from app.services.editorial_gate import evaluate_article
    v = evaluate_article(_FakeArticle(), "UFC")
    assert v.post_worthy is False
    assert v.engagement == "medium"


# ── Test 4: no JSON in reply → ValueError ──

def test_no_json_raises_valueerror(monkeypatch):
    monkeypatch.setattr(
        "app.services.editorial_gate.generate_caption",
        lambda prompt: ("I cannot decide", "m"),
    )
    from app.services.editorial_gate import evaluate_article
    with pytest.raises(ValueError):
        evaluate_article(_FakeArticle(), "F1")


# ── Test 5: _9router_fetch_markdown is never called ──

def test_no_web_search_call(monkeypatch):
    monkeypatch.setattr(
        "app.services.editorial_gate.generate_caption",
        lambda prompt: ('{"post_worthy": true, "engagement": "low", "reason": "ok"}', "m"),
    )
    monkeypatch.setattr(
        "app.services.image_downloader._9router_fetch_markdown",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("_9router_fetch_markdown must not be called")),
    )
    from app.services.editorial_gate import evaluate_article
    evaluate_article(_FakeArticle(), "F1")  # must not raise
