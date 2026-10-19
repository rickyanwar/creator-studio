"""Shared data contracts for the Pro poster pipeline (brief → photos → art direction → generate → QA).

Every step of the pipeline exchanges these plain dataclasses, so each module can be built and
tested on its own. Owner rules that shape them: a wrong person or a wrong event is worse than no
post (fail closed), real faces are never changed, and posters must not look AI-made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StoryType = Literal[
    "result", "quote", "transfer", "crash", "controversy", "preview", "feature", "breaking", "other"
]
Verdict = Literal["yes", "no", "unsure", "error"]


@dataclass(frozen=True)
class Subject:
    name: str                       # full name as written in the article, e.g. "Max Verstappen"
    role: Literal["hero", "secondary", "mentioned"] = "hero"
    team: str | None = None         # e.g. "Verstappen Racing", "McLaren"


@dataclass(frozen=True)
class EventContext:
    series: str                     # exact series, e.g. "Formula 1", "MotoGP", "WorldSBK", "GT World Challenge Europe", "UFC"
    vehicle: str | None = None      # e.g. "Formula 1 car", "GT3 car", "MotoGP bike", "superbike"; None for combat sports
    circuit: str | None = None      # e.g. "Portimao"; venue for UFC
    country: str | None = None
    session: str | None = None      # race / qualifying / practice / sprint / press / fight night ...
    year: int | None = None
    night: bool | None = None
    rain: bool | None = None


@dataclass(frozen=True)
class PosterBrief:
    """What the poster must show, decided from the article text only (never from photos)."""
    job_id: int
    subjects: tuple[Subject, ...]   # empty = no person on the poster (typographic / vehicle-only)
    event: EventContext
    story_type: StoryType
    mood: Literal["serious", "happy", "tense", "dramatic", "neutral"]
    badge_label: str                # the pill text, e.g. "F1 NEWS", "WORLDSBK NEWS", "GT WORLD CHALLENGE", "UFC NEWS"
    accent_colors: tuple[str, ...] = ()   # hex team / series colours, e.g. ("#FF8000",)
    visual_ideas: tuple[str, ...] = ()    # short art-direction hints from the story, e.g. "chequered flag", "pit wall"
    must_avoid: tuple[str, ...] = ()      # e.g. "F1 cars" for a GT3 story, "Singapore night race"
    language: str = "en"


@dataclass(frozen=True)
class PhotoCandidate:
    source: Literal["story", "gallery"]
    gallery_image_id: int | None    # None for the article's own photo
    path_or_url: str                # local path for gallery images, URL or temp path for the story photo
    role: Literal["hero", "context"]  # hero = shows the subject person; context = shows the event (car/bike/venue)
    caption: str | None = None      # trusted photo-agency caption (Getty slug), e.g. "nicolo bulega of aruba it racing ducati"


@dataclass(frozen=True)
class PhotoCheck:
    candidate: PhotoCandidate
    person: Verdict                 # is the hero subject in it? ("no" also when no person is expected but one dominates)
    event_match: Verdict            # does it match brief.event (series, vehicle type, venue, night/rain, era)?
    obstructed: bool                # foreground object/person covering the subject
    watermark: bool
    quality: int                    # 1–5 overall photo quality for a poster
    notes: str = ""
    face_frontal: bool = False      # the hero's face is clearly visible and turned toward the camera
    shows: str = ""                 # one-line description from the vision check, e.g. "Bulega lifting the trophy on the Jerez podium"
    story_fit: int = 0              # 1–5 how well this photo supports THIS story (0 = not rated)

    @property
    def usable_as_hero(self) -> bool:
        return self.person == "yes" and self.event_match != "no" and not self.obstructed and not self.watermark and self.quality >= 3

    @property
    def usable_as_context(self) -> bool:
        return self.event_match == "yes" and not self.watermark and self.quality >= 3


@dataclass(frozen=True)
class PhotoSelection:
    hero: PhotoCheck | None         # None = no verified photo of the subject → no person on the poster
    context: tuple[PhotoCheck, ...] = ()   # 0–2 verified event photos
    rejected: tuple[PhotoCheck, ...] = ()  # kept for the report / logs
    face_ref: PhotoCheck | None = None     # verified photo of the hero from ANOTHER event: face/likeness only, set only when hero is None
    portrait: PhotoCheck | None = None     # extra verified front-facing face photo of the hero (any event) when the hero photo shows no clear face


@dataclass(frozen=True)
class PosterConcept:
    """Story-specific visual idea written by the text model before the single image generation."""
    key_moment: str                 # the one moment/result the poster must communicate
    scene: str                      # what the main image shows and which reference it uses
    inset: str                      # what the circular inset shows (must differ from the main image) or "none"
    graphic_words: tuple[str, ...] = ()   # ≤3 short words/numbers, each verified to appear in the headline/story
    palette: str = ""               # e.g. "Ducati red, deep black, warm gold"
    mood: str = ""


@dataclass(frozen=True)
class ArtDirection:
    layout: Literal["hero_with_context", "hero_solo", "action_full_bleed", "face_ref_scene", "duo", "typographic"]
    prompt: str                     # full prompt for the image engine (no headline text in the image)
    refs: tuple[str, ...]           # reference image paths in the order the prompt names them
    size: str = "1024x1536"
    allowed_text: tuple[str, ...] = ()   # the only decorative words the engine may draw, e.g. ("BULEGA",)


@dataclass(frozen=True)
class QAResult:
    passed: bool
    scores: dict[str, int] = field(default_factory=dict)   # identity, event, ai_look, composition, text_zone: 1–5 each
    issues: tuple[str, ...] = ()
    fix_hint: str = ""              # one sentence the art director can append on the single retry
    identity_passed: bool | None = None   # from identity_check.check_identity when a hero is used


@dataclass(frozen=True)
class ProPosterResult:
    image: bytes | None             # final 1080x1350 PNG of the AI poster, None when it fell back
    brief: PosterBrief
    selection: PhotoSelection
    direction: ArtDirection | None
    qa: QAResult | None
    engine: str | None              # "gptcli" | "9router" | None
    attempts: int                   # image generations used (max 2)
    fallback_reason: str | None = None   # set when image is None → caller renders the typographic card
    attempt_log: tuple[dict, ...] = ()
