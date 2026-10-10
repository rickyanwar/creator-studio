import pytest
from app.services.radar_clustering import (
    normalize_text,
    token_set,
    jaccard,
    compute_phash,
    phash_distance,
    PostSig,
    same_story,
    assign_story,
    captions_too_similar,
    quote_matches_source
)
from PIL import Image
import numpy as np
import io

def test_normalize_text():
    assert normalize_text("Hello World!") == "hello world"
    assert normalize_text("Check https://example.com @user #tag123") == "check"
    assert normalize_text("Café and naïve 123") == "café and naïve 123"
    assert normalize_text("  Space   collapse  \n test ") == "space collapse test"
    assert normalize_text("Emoji 😊 test!") == "emoji test"

def test_token_set():
    ts = token_set("This is a simple test with indonesian di dan yang")
    # 'this', 'is', 'a', 'with', 'di', 'dan', 'yang' might be stopwords
    assert "simple" in ts
    assert "test" in ts
    assert "yang" not in ts # Stopword

def test_jaccard():
    a = frozenset(["a", "b", "c"])
    b = frozenset(["b", "c", "d"])
    assert jaccard(a, b) == 2.0 / 4.0
    assert jaccard(frozenset(), frozenset()) == 0.0

def create_test_image(mode="h", size=(100, 100)):
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    if mode == "rand":
        arr = np.random.randint(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    else:
        for y in range(size[1]):
            for x in range(size[0]):
                if mode == "h":
                    val = int((x / size[0]) * 255)
                else:
                    val = int((y / size[1]) * 255)
                arr[y, x] = [val, val, val]
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()

def test_compute_phash():
    b1 = create_test_image(mode="rand")
    b2 = create_test_image(mode="rand") # A different random image
    # They should have high distance since they are different random noises
    h1 = compute_phash(b1)
    h2 = compute_phash(b2)
    assert len(h1) == 16
    assert type(h1) is str
    assert phash_distance(h1, h2) > 10
    
    # Same random image, slightly resized
    b3_resized = Image.open(io.BytesIO(b1)).resize((99, 99)).convert('RGB')
    buf = io.BytesIO()
    b3_resized.save(buf, format='JPEG')
    b3 = buf.getvalue()
    
    h3 = compute_phash(b3)
    assert phash_distance(h1, h3) <= 10
    
    with pytest.raises(ValueError):
        compute_phash(b"not an image")

def test_same_story():
    p1 = PostSig(key="1", account_key="A", phash="0000000000000000", text="breaking news today")
    p2 = PostSig(key="2", account_key="B", phash="0000000000000001", text="some other text entirely")
    
    # phash distance 1 -> same
    assert same_story(p1, p2) == "same"
    
    # phash diff > 10, jaccard >= 0.5 -> same
    p3 = PostSig(key="3", account_key="C", phash="FFFFFFFFFFFFFFFF", text="breaking news today something")
    assert same_story(p1, p3) == "same"
    
    # 0.25 <= jaccard < 0.5 -> use judge
    p4 = PostSig(key="4", account_key="D", phash="FFFFFFFFFFFFFFFF", text="breaking news happening") # jaccard slightly lower
    def fake_judge(a, b): return True
    # If token_set("breaking news today") = {"breaking", "news", "today"} (len 3)
    # token_set("breaking news happening") = {"breaking", "news", "happening"} (len 3)
    # intersection = 2, union = 4 -> jaccard = 0.5 -> actually that's >= 0.5.
    
    # Let's craft it
    # p1: "breaking news today morning early" -> 5
    # p4: "breaking news tomorrow evening late" -> 5
    # intersection = 2 (breaking, news), union = 8 -> 0.25
    p1_craft = PostSig(key="1", account_key="A", phash="0000000000000000", text="breaking news today morning early")
    p4_craft = PostSig(key="4", account_key="D", phash="FFFFFFFFFFFFFFFF", text="breaking news tomorrow evening late")
    assert same_story(p1_craft, p4_craft, judge=fake_judge) == "same"
    assert same_story(p1_craft, p4_craft, judge=lambda a,b: False) == "different"
    assert same_story(p1_craft, p4_craft, judge=None) == "uncertain"
    
    # jaccard < 0.25 -> different
    p5 = PostSig(key="5", account_key="E", phash="FFFFFFFFFFFFFFFF", text="completely unrelated topic entirely")
    assert same_story(p1_craft, p5) == "different"

def test_assign_story():
    p1 = PostSig(key="1", account_key="A", phash="0000000000000000", text="breaking news today")
    p2 = PostSig(key="2", account_key="B", phash="FFFFFFFFFFFFFFFF", text="totally different")
    stories = [
        (100, [p1]),
        (200, [p2])
    ]
    p_new = PostSig(key="3", account_key="C", phash="0000000000000001", text="unrelated")
    assert assign_story(p_new, stories) == 100
    
    p_unrelated = PostSig(key="4", account_key="C", phash="8888888888888888", text="new stuff here now")
    assert assign_story(p_unrelated, stories) is None

def test_captions_too_similar():
    # jaccard >= 0.35
    a = "this is a very specific caption about a dog"
    b = "this is a very specific caption about a cat"
    # shared: specific, caption, about -> 3. union -> dog, cat -> 5. j=0.6
    assert captions_too_similar(a, b) is True
    
    # 8 consecutive words match
    a = "one two three four five six seven eight nine ten"
    b = "apple banana one two three four five six seven eight carrot"
    # jaccard might be low, but run of 8 matches
    assert captions_too_similar(a, b) is True
    
    a = "one two three four five six seven"
    b = "apple banana one two three four five six seven carrot"
    # run of 7 -> False (if jaccard < 0.35)
    # union=9, intersection=7 -> jaccard = 7/9 > 0.35. Let's make jaccard low.
    a = "one two three four five six seven" + " apple banana cherry date elder fig grape"
    b = "one two three four five six seven" + " kiwi lemon mango nectar orange plum quince"
    assert captions_too_similar(a, b) is False

def test_quote_matches_source():
    source = ["This is a long statement. " + "Here is the actual quote that we want to match exactly in this long text" + " and then it ends."]

    # Exact contiguous substring after normalization
    quote1 = "Here is the actual quote that we want to match exactly in this long text"
    assert quote_matches_source(quote1, source) is True

    # Case/punctuation differences
    quote2 = "Here is the ACTUAL quote, that we want to match EXACTLY in this long text!"
    assert quote_matches_source(quote2, source) is True

    # Jaccard window match (1 word different out of 15 words -> intersection 14, union 16 -> 14/16 = 0.875 >= 0.85)
    quote3 = "Here is the factual quote that we want to match exactly in this long text" # changed 'actual' to 'factual'
    assert quote_matches_source(quote3, source) is True

    # Truncated or different
    quote4 = "This is a completely different sentence"
    assert quote_matches_source(quote4, source) is False
    
    # Boundary check (e.g., 'is a' shouldn't match inside 'this a')
    assert quote_matches_source("is a", ["this a"]) is False
    
    assert quote_matches_source("", source) is False
