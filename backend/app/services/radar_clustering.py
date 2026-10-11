"""Pure functions for the Viral Radar clustering, see PLAN.md §6.3/§6.5."""

import re
from dataclasses import dataclass
from typing import Sequence, Callable
import cv2
import numpy as np
from PIL import Image
import io

def normalize_text(text: str) -> str:
    """Normalize text by lowercasing, removing URLs, tags, mentions, and keeping only letters/digits."""
    text = text.lower()
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = text.replace('_', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

STOPWORDS = frozenset([
    # English
    "the", "and", "for", "with", "that", "this", "are", "you", "was", "not", "have", "from",
    "but", "what", "all", "were", "when", "can", "has", "there", "been", "one", "more", "out",
    "who", "which", "their", "will", "about", "how", "they",
    # Indonesian
    "dan", "di", "yang", "ke", "dari", "ini", "itu", "untuk", "dengan", "pada", "juga", "ada",
    "tidak", "bisa", "akan", "dalam", "sebagai", "sudah", "karena", "saat", "oleh", "atau",
    "lebih", "lagi", "sama", "kita", "banyak"
])

def token_set(text: str) -> frozenset[str]:
    """Extract a set of non-stopword tokens >= 3 characters from text."""
    norm = normalize_text(text)
    words = norm.split()
    return frozenset(w for w in words if len(w) >= 3 and w not in STOPWORDS)

def jaccard(a: frozenset, b: frozenset) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)

def compute_phash(image_bytes: bytes) -> str:
    """Compute 64-bit perceptual hash of an image."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert('L')
        img = img.resize((32, 32), Image.Resampling.LANCZOS)
    except Exception as e:
        raise ValueError("undecodable bytes") from e
        
    arr = np.array(img, dtype=np.float32)
    dct = cv2.dct(arr)
    dctlowfreq = dct[:8, :8]
    med = np.median(dctlowfreq.flatten()[1:])
    
    hash_val = 0
    idx = 0
    for i in range(8):
        for j in range(8):
            if dctlowfreq[i, j] > med:
                hash_val |= (1 << idx)
            idx += 1
            
    return f"{hash_val:016x}"

def phash_distance(a: str, b: str) -> int:
    """Calculate Hamming distance between two perceptual hashes."""
    val_a = int(a, 16)
    val_b = int(b, 16)
    diff = val_a ^ val_b
    return bin(diff).count('1')

@dataclass(frozen=True)
class PostSig:
    key: str
    account_key: str
    phash: str | None
    text: str

def same_story(a: PostSig, b: PostSig, judge: Callable[[str, str], bool] | None = None) -> str:
    """Determine if two posts represent the same story based on phash and text similarity."""
    if a.phash and b.phash and phash_distance(a.phash, b.phash) <= 10:
        return 'same'
        
    ts_a = token_set(a.text)
    ts_b = token_set(b.text)
    j = jaccard(ts_a, ts_b)
    
    if j >= 0.5:
        return 'same'
    elif 0.25 <= j < 0.5:
        if judge:
            return 'same' if judge(a.text, b.text) else 'different'
        return 'uncertain'
    else:
        return 'different'

def assign_story(new: PostSig, stories: Sequence[tuple[int, Sequence[PostSig]]], judge=None) -> int | None:
    """Assign a post to the best matching story based on sort key, or None if no match."""
    best_key = None
    best_story_id = None
    
    for story_id, members in stories:
        for member in members:
            if same_story(new, member, judge) == 'same':
                if new.phash and member.phash:
                    dist = phash_distance(new.phash, member.phash)
                else:
                    dist = 65
                    
                j = jaccard(token_set(new.text), token_set(member.text))
                sort_key = (dist, -j)
                
                if best_key is None or sort_key < best_key:
                    best_key = sort_key
                    best_story_id = story_id
                    
    return best_story_id

def captions_too_similar(a: str, b: str) -> bool:
    """Check if two captions are too similar (Jaccard >= 0.35 or >= 8 consecutive matching words)."""
    ts_a = token_set(a)
    ts_b = token_set(b)
    if jaccard(ts_a, ts_b) >= 0.35:
        return True
        
    norm_a = normalize_text(a).split()
    norm_b = normalize_text(b).split()
    
    for i in range(len(norm_a) - 7):
        run = norm_a[i:i+8]
        run_len = len(run)
        for j in range(len(norm_b) - run_len + 1):
            if norm_b[j:j+run_len] == run:
                return True
                
    return False

def quote_matches_source(quote: str, source_texts: Sequence[str]) -> bool:
    """Check if a quote is faithfully derived from source text (contiguous match or high Jaccard window)."""
    if not quote:
        return False
        
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return False
        
    quote_words = norm_quote.split()
    quote_len = len(quote_words)
    padded_quote = f" {norm_quote} "
    
    for text in source_texts:
        norm_text = normalize_text(text)
        padded_text = f" {norm_text} "
        
        if padded_quote in padded_text:
            return True
            
        text_words = norm_text.split()
        
        if len(text_words) >= quote_len:
            quote_ts = frozenset(quote_words)
            for i in range(len(text_words) - quote_len + 1):
                window = text_words[i:i+quote_len]
                window_ts = frozenset(window)
                if jaccard(quote_ts, window_ts) >= 0.85:
                    return True
                    
    return False
