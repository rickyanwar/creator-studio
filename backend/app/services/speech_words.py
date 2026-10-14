"""Word timings from the real Mode 8 voice-over (faster-whisper on CPU, model cached on disk)."""
import difflib
import logging
import os
import re
import unicodedata
from typing import Optional

from app.services.yt_transcript import Word

logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("CAPTION_ASR_MODEL", "base")
# yt_private volume: private, persistent, never served by nginx (see docker-compose.server.yml worker-video)
CACHE_DIR = os.getenv("CAPTION_ASR_CACHE", "/var/lib/studio-yt/whisper-models")
MIN_WORD_RATIO = 0.5


def _get_model():
    from faster_whisper import WhisperModel
    return WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=2, download_root=CACHE_DIR)


def transcribe_words(media_path: str, language: Optional[str]) -> Optional[list[Word]]:
    """Spoken words with start/end seconds, or None when transcription is unavailable."""
    import gc

    model = None
    try:
        model = _get_model()
        segments, _info = model.transcribe(
            media_path, language=language or None, word_timestamps=True, vad_filter=True, beam_size=1,
        )
        words = [Word(float(w.start), float(w.end), w.word.strip())
                 for segment in list(segments) for w in (segment.words or []) if w.word and w.word.strip()]
        return words or None
    except Exception as exc:  # never break finalize because of captions
        logger.warning("Caption ASR failed for %s: %s", media_path, exc)
        return None
    finally:
        if model is not None:
            del model
        gc.collect()


def _norm(token: str) -> str:
    token = unicodedata.normalize("NFKD", token.lower())
    token = "".join(c for c in token if not unicodedata.combining(c))
    return re.sub(r"[^\w]", "", token)


def align_to_script(spoken: list[Word], script_texts: list[str]) -> list[Word]:
    """Align spoken words to script: equal uses script spelling with spoken timing;
    replace spreads script words across spoken span weighted by token length;
    delete keeps unmatched spoken words; insert drops unspoken script words."""
    script = [t for text in script_texts for t in (text or "").split()]
    a = [_norm(w.text) for w in spoken]
    b = [_norm(t) for t in script]
    result: list[Word] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            result.extend(Word(spoken[i].start, spoken[i].end, script[j]) for i, j in zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            t_start = spoken[i1].start
            t_end = spoken[i2 - 1].end
            span = max(t_end - t_start, 0.0)
            sub = script[j1:j2]
            weights = [max(len(tok), 1) for tok in sub]
            total_w = sum(weights) or 1
            cur = t_start
            for idx, (tok, w) in enumerate(zip(sub, weights)):
                nxt = t_end if idx == len(sub) - 1 else cur + span * (w / total_w)
                result.append(Word(cur, nxt, tok))
                cur = nxt
        elif tag == "delete":
            result.extend(spoken[i1:i2])
        # "insert": script words that were never spoken are dropped
    return result


def caption_words(media_path: str, language: Optional[str], script_texts: list[str]) -> Optional[list[Word]]:
    """Caption words that follow the real voice, or None to fall back to the estimate."""
    spoken = transcribe_words(media_path, language)
    script_count = sum(len((t or "").split()) for t in script_texts)
    if not spoken or (script_count and len(spoken) < MIN_WORD_RATIO * script_count):
        return None
    return align_to_script(spoken, script_texts)
