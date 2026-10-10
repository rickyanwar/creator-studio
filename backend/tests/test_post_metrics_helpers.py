import pytest
from app.services.post_metrics import (
    MetricCounts,
    parse_statistic,
    extract_post_id,
    due_bucket,
    bucket_tolerance,
    BUCKETS
)


def test_metric_counts_engagement():
    assert MetricCounts(likes=None, comments=None, shares=None).engagement is None
    assert MetricCounts(likes=10, comments=None, shares=None).engagement == 10
    assert MetricCounts(likes=10, comments=20, shares=5).engagement == 35
    assert MetricCounts(likes=None, comments=5, shares=0).engagement == 5


def test_due_bucket():
    # Outside any window
    assert due_bucket(0, set()) is None
    assert due_bucket(59, set()) is None
    assert due_bucket(76, set()) is None # 1h + 15
    
    # 1h window: [60, 75]
    assert due_bucket(60, set()) == "1h"
    assert due_bucket(75, set()) == "1h"
    assert due_bucket(60, {"1h"}) is None

    # 6h window: [360, 396] (tolerance max(15, 36))
    assert due_bucket(360, set()) == "6h"
    assert due_bucket(396, set()) == "6h"
    assert due_bucket(397, set()) is None
    assert due_bucket(360, {"1h", "6h"}) is None

    # 24h window: [1440, 1584] (tolerance 144)
    assert due_bucket(1440, set()) == "24h"
    assert due_bucket(1584, set()) == "24h"

    # 72h window: [4320, 4752] (tolerance 432)
    assert due_bucket(4320, set()) == "72h"

    # 7d window: [10080, 11088] (tolerance 1008)
    assert due_bucket(10080, set()) == "7d"
    assert due_bucket(11088, set()) == "7d"


def test_parse_statistic_shapes():
    # 1. Flat ints
    assert parse_statistic({"likes": 10, "comments": 5, "shares": 2}) == MetricCounts(10, 5, 2)
    # 2. String digits
    assert parse_statistic({"reactionCount": "100", "commentCount": "50", "shareCount": "25"}) == MetricCounts(100, 50, 25)
    # 3. Dict values
    assert parse_statistic({"like": {"count": 7}, "comment": {"total": 3}, "share": {"value": 1}}) == MetricCounts(7, 3, 1)
    # 4. Nested in "data"
    assert parse_statistic({"data": {"reactions": 15, "comments": 5, "shares": 0}, "garbage": True}) == MetricCounts(15, 5, 0)
    # 5. Nested in "statistic"
    assert parse_statistic({"statistic": {"likeCount": "99"}, "likes": "should_ignore_if_outer_does_not_match_before_inner"}) == MetricCounts(99, None, None)
    # 6. Garbage
    assert parse_statistic(None) == MetricCounts(None, None, None)
    assert parse_statistic("foo") == MetricCounts(None, None, None)
    assert parse_statistic({"foo": "bar"}) == MetricCounts(None, None, None)
    assert parse_statistic({"data": "string"}) == MetricCounts(None, None, None)
    
    # Target fallback check if inner dict has none of the keys
    assert parse_statistic({"data": {"x": 1}, "likes": 10}) == MetricCounts(10, None, None)


def test_extract_post_id():
    assert extract_post_id(None) is None
    assert extract_post_id({"foo": "bar"}) is None
    
    # Top level string
    assert extract_post_id({"postId": "123_456"}) == "123_456"
    assert extract_post_id({"post_id": "123_456"}) == "123_456"
    assert extract_post_id({"postId": "123"}) == "123"
    
    # Top level int
    assert extract_post_id({"postId": 123456}) == "123456"
    
    # Nested in data
    assert extract_post_id({"data": {"postId": "999_888"}}) == "999_888"
    
    # Nested in result
    assert extract_post_id({"result": {"post_id": "777"}}) == "777"
    
    # Invalid strings
    assert extract_post_id({"postId": "abc_def"}) is None
    assert extract_post_id({"postId": "123_456_789"}) is None
