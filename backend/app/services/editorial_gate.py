"""Editorial gate — one AI call per article to decide whether a scraped news
article should become a designed image post. No fact-checking: the model's
knowledge lags the current season and it rejected real news as fabricated
(removed 2026-10-31, owner decision). Rejects only off-niche, purely
promotional, or empty articles. Opt-in per fan page
(TargetFanpage.mode2_editorial_gate_enabled). Exceptions let the article
through — see news_copywriter.copywrite_article.
"""

import json
import logging
import re
from dataclasses import dataclass

from app.services.ai_caption import generate_caption

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 3000

_ENGAGEMENT_LEVELS = ("low", "medium", "high")


@dataclass
class EditorialVerdict:
    post_worthy: bool
    reason: str
    fact_check: str       # always "off" (fact-checking removed 2026-10-31, owner decision)
    fact_check_note: str
    engagement: str = "medium"  # one of _ENGAGEMENT_LEVELS


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    return json.loads(m.group(0))


def _judge_post_worthiness(title: str, content: str, niche: str) -> tuple[bool, str, str]:
    """Decide whether the article should become an image post and predict
    engagement. Returns (post_worthy, reason, engagement)."""
    prompt = f"""You are the editor of a {niche} Facebook fan page deciding whether a news article should become a designed image post.

HEADLINE: {title}
CONTENT:
{content[:_MAX_CONTENT_CHARS]}

IMPORTANT: Do NOT judge whether the facts in the article are true. Your knowledge is out of date: current seasons, team line-ups, results, standings, records and transfers may differ from what you remember. Treat every name, team, result, date and statistic in the article as correct. Never reject an article as fabricated, false, incorrect, unverified, made up, or contradicting what you know.

Reject ("post_worthy": false) ONLY if one of these is clearly true:
- the article is not about {niche} (another sport, another racing series, or nothing a {niche} fan would follow);
- it is purely promotional: an advert, a betting or ticket offer, a product or shop promotion;
- it has no real story: empty, garbled, or only a list of links or headlines.
Otherwise approve ("post_worthy": true). Rumours, opinions, quotes and small updates about {niche} are approved.

Also predict engagement for {niche} fans: "high" for results, transfers, injuries, controversies, strong quotes and milestones; "low" for minor routine items; otherwise "medium".

Reply with ONLY a JSON object: {{"post_worthy": true|false, "engagement": "low|medium|high", "reason": "one short sentence"}}"""
    raw, _ = generate_caption(prompt)
    data = _parse_json(raw)
    post_worthy = bool(data.get("post_worthy"))
    engagement = str(data.get("engagement") or "medium").strip().lower()
    if engagement not in _ENGAGEMENT_LEVELS:
        engagement = "medium"
    return post_worthy, str(data.get("reason") or "").strip(), engagement


def evaluate_article(article, niche: str) -> EditorialVerdict:
    """Run the editorial judgment for one article. Raises on AI/parse failure —
    callers should catch and degrade (treat as post_worthy) rather than let a
    flaky call block the whole pipeline; see news_copywriter.copywrite_article."""
    title = article.scraped_title or ""
    content = article.scraped_content or ""

    post_worthy, reason, engagement = _judge_post_worthiness(title, content, niche)

    return EditorialVerdict(
        post_worthy=post_worthy, reason=reason,
        fact_check="off", fact_check_note="",
        engagement=engagement,
    )
