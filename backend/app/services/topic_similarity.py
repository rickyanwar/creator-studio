import re

STOP_WORDS = {
    "the", "a", "an", "of", "in", "on", "and", "or", "to", "for", "with",
    "how", "why", "what", "your", "its", "new", "f1", "motogp", "2025", "2026", "2027",
}


def subject_of(topic: str) -> str:
    return topic.split(":", 1)[0].strip()


def subject_tokens(topic: str) -> set[str]:
    s = subject_of(topic).lower()
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    tokens = set()
    for w in words:
        if w in STOP_WORDS or len(w) < 3:
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        if w not in STOP_WORDS and len(w) >= 3:
            tokens.add(w)
    return tokens


def _token_matches(t1: str, t2: str) -> bool:
    if t1 == t2:
        return True
    shorter, longer = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    return len(shorter) >= 4 and longer.startswith(shorter)


def similar_subjects(a: str, b: str) -> bool:
    tokens_a = subject_tokens(a)
    tokens_b = subject_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    if len(tokens_a) <= len(tokens_b):
        smaller, larger = tokens_a, tokens_b
    else:
        smaller, larger = tokens_b, tokens_a

    matched = sum(1 for s in smaller if any(_token_matches(s, l) for l in larger))
    return (matched / len(smaller)) >= 0.6
