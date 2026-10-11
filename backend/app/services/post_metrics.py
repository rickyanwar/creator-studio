import re
from dataclasses import dataclass
from typing import Any


BUCKETS = (("1h", 60), ("6h", 360), ("24h", 1440), ("72h", 4320), ("7d", 10080))
BACKFILL_BUCKET = "backfill"


def bucket_tolerance(threshold: int) -> int:
    return max(15, threshold // 10)


TRACK_WINDOW_MINUTES = 10080 + bucket_tolerance(10080)


def due_bucket(age_minutes: int, taken: set[str]) -> str | None:
    for name, threshold in BUCKETS:
        if name in taken:
            continue
        if threshold <= age_minutes <= threshold + bucket_tolerance(threshold):
            return name
    return None


@dataclass(frozen=True)
class MetricCounts:
    likes: int | None
    comments: int | None
    shares: int | None

    @property
    def engagement(self) -> int | None:
        if self.likes is None and self.comments is None and self.shares is None:
            return None
        return (self.likes or 0) + (self.comments or 0) + (self.shares or 0)


def _extract_count(data: dict, keys: list[str]) -> int | None:
    for key in keys:
        if key in data:
            val = data[key]
            if isinstance(val, int):
                return val
            if isinstance(val, str) and val.isdigit():
                return int(val)
            if isinstance(val, dict):
                for sub_key in ("count", "total", "value"):
                    if sub_key in val:
                        sub_val = val[sub_key]
                        if isinstance(sub_val, int):
                            return sub_val
                        if isinstance(sub_val, str) and sub_val.isdigit():
                            return int(sub_val)
    return None


def parse_statistic(payload: Any) -> MetricCounts:
    if not isinstance(payload, dict):
        return MetricCounts(None, None, None)

    # Find the target dict
    target = payload
    for key in ("data", "statistic", "statistics"):
        if key in payload and isinstance(payload[key], dict):
            # Check if this sub-dict has any of our keys before picking it
            sub = payload[key]
            all_keys = [
                "like", "likes", "reaction", "reactions", "reactionCount", "likeCount",
                "comment", "comments", "commentCount",
                "share", "shares", "shareCount"
            ]
            if any(k in sub for k in all_keys):
                target = sub
                break

    likes = _extract_count(
        target,
        ["like", "likes", "reaction", "reactions", "reactionCount", "likeCount"]
    )
    comments = _extract_count(
        target,
        ["comment", "comments", "commentCount"]
    )
    shares = _extract_count(
        target,
        ["share", "shares", "shareCount"]
    )

    return MetricCounts(likes=likes, comments=comments, shares=shares)


def extract_post_id(repliz_response_json: dict | None) -> str | None:
    if not isinstance(repliz_response_json, dict):
        return None

    def _find_id(data: dict) -> str | None:
        for key in ("postId", "post_id"):
            if key in data:
                val = data[key]
                if isinstance(val, int):
                    val = str(val)
                if isinstance(val, str) and re.match(r"^\d+(_\d+)?$", val):
                    return val
        return None

    res = _find_id(repliz_response_json)
    if res:
        return res

    for sub_key in ("data", "result"):
        if sub_key in repliz_response_json and isinstance(repliz_response_json[sub_key], dict):
            res = _find_id(repliz_response_json[sub_key])
            if res:
                return res

    return None
