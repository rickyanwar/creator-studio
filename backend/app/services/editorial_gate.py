"""Editorial gate — before spending a design render + a Facebook post on a
scraped article, ask 9Router whether it's actually worth posting: a
web-search-grounded fact-check pass, then a separate post-worthiness /
engagement judgment that folds in the fact-check verdict.

Two AI calls (plus one web-fetch) per (article, fanpage) — kept as two
focused calls rather than one combined prompt so each gets the model's full
attention: fact-checking is a narrow, literal task, and editorial judgment is
a broader, more subjective one, and mixing them tends to blur both.

Opt-in per fanpage (TargetFanpage.mode2_editorial_gate_enabled) — off by
default so it never silently changes behavior for a fanpage that hasn't
turned it on. See news_copywriter.copywrite_article for wiring; a raised
exception here is treated as "let it through" by the caller so a flaky
9Router call never silently blocks every article.
"""

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from app.services.ai_caption import generate_caption

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 3000
_MAX_SEARCH_CHARS = 3000

_FACT_CHECK_VERDICTS = ("consistent", "outdated", "contradicted", "unverifiable")
_ENGAGEMENT_LEVELS = ("low", "medium", "high")


@dataclass
class EditorialVerdict:
    post_worthy: bool
    reason: str
    fact_check: str       # one of _FACT_CHECK_VERDICTS
    fact_check_note: str
    engagement: str = "medium"  # one of _ENGAGEMENT_LEVELS


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    return json.loads(m.group(0))


def _search_snippets(query: str) -> str:
    """General web search (not the Getty/Google-Images gallery search) via the
    same 9Router web-fetch used for stock photos — grounds the fact-check in
    what's actually indexed right now instead of relying on the model's
    training-data knowledge, which lags real time."""
    from app.config import get_settings
    from app.services.image_downloader import _9router_fetch_markdown

    s = get_settings()
    url = s.editorial_factcheck_search_url_template.format(query=quote(query))
    try:
        return _9router_fetch_markdown(url, context="editorial_factcheck", keyword=query[:128])[:_MAX_SEARCH_CHARS]
    except Exception as exc:
        logger.warning("Editorial gate: search failed for %r: %s", query, exc)
        return ""


def _fact_check(title: str, content: str) -> tuple[str, str]:
    """Call 1: search the web for the headline, then ask AI to assess the
    article's claim against those results. Returns (verdict, note)."""
    snippets = _search_snippets(title)
    prompt = f"""You are a fact-checker for a sports news Facebook page.

ARTICLE HEADLINE: {title}
ARTICLE CONTENT:
{content[:_MAX_CONTENT_CHARS]}

LIVE WEB SEARCH RESULTS for this headline (snippets may be noisy or partly irrelevant — use your judgement):
{snippets or "(no search results available)"}

Assess whether this article's core claim holds up against the search results:
- "consistent": the search results corroborate the claim, or the story is too recent to be indexed yet but nothing contradicts it
- "outdated": the claim was true but has since been superseded (e.g. a transfer/deal that later fell through, a result later overturned, an injury that's since resolved)
- "contradicted": the search results directly conflict with the claim
- "unverifiable": not enough signal either way to judge

Reply with ONLY a JSON object: {{"verdict": "consistent|outdated|contradicted|unverifiable", "note": "one short sentence explaining why"}}"""
    raw, _ = generate_caption(prompt)
    data = _parse_json(raw)
    verdict = str(data.get("verdict") or "unverifiable").strip().lower()
    if verdict not in _FACT_CHECK_VERDICTS:
        verdict = "unverifiable"
    return verdict, str(data.get("note") or "").strip()


def _judge_post_worthiness(title: str, content: str, niche: str, fact_check: str, fact_check_note: str) -> tuple[bool, str, str]:
    """Call 2: given the article AND the fact-check verdict, decide whether to
    turn it into an image post, and predict engagement. Returns
    (post_worthy, reason, engagement)."""
    prompt = f"""You are the editor of a {niche} Facebook fan page deciding whether an article is worth turning into a designed image post.

HEADLINE: {title}
CONTENT:
{content[:_MAX_CONTENT_CHARS]}

FACT-CHECK RESULT: {fact_check} — {fact_check_note or "no note"}

Decide:
- Reject ("post_worthy": false) if the fact-check verdict is "contradicted" or "outdated", or the article itself is low-value: an unsubstantiated rumor, duplicate/stale news, purely promotional content, or too niche/boring to get real engagement.
- Otherwise approve if it's genuinely newsworthy for {niche} fans and likely to get likes/comments/shares — results, transfers, injuries, controversies, strong quotes, milestones.

Reply with ONLY a JSON object: {{"post_worthy": true|false, "engagement": "low|medium|high", "reason": "one short sentence"}}"""
    raw, _ = generate_caption(prompt)
    data = _parse_json(raw)
    post_worthy = bool(data.get("post_worthy"))
    engagement = str(data.get("engagement") or "medium").strip().lower()
    if engagement not in _ENGAGEMENT_LEVELS:
        engagement = "medium"
    return post_worthy, str(data.get("reason") or "").strip(), engagement


def evaluate_article(article, niche: str) -> EditorialVerdict:
    """Run both passes for one article. Raises on AI/parse failure — callers
    should catch and degrade (treat as post_worthy) rather than let a flaky
    call block the whole pipeline; see news_copywriter.copywrite_article."""
    title = article.scraped_title or ""
    content = article.scraped_content or ""

    fact_check, fact_check_note = _fact_check(title, content)
    post_worthy, reason, engagement = _judge_post_worthiness(title, content, niche, fact_check, fact_check_note)

    return EditorialVerdict(
        post_worthy=post_worthy, reason=reason,
        fact_check=fact_check, fact_check_note=fact_check_note,
        engagement=engagement,
    )
