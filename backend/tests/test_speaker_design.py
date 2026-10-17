import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.services import speaker_design
from app.services.visual_engine import GenerationResult


def _as_result(fn):
    def wrapper(db, req, **kwargs):
        kwargs.pop("exclude", None)
        return GenerationResult(fn(db, req, **kwargs), "9router", {}, None)
    return wrapper


def image_bytes(size=(200, 300), color=(40, 80, 120)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_speaker_side_uses_yaw_rule(monkeypatch):
    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"yaw": -0.04})
    assert speaker_design.speaker_side(b"speaker") == "right"

    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"yaw": 0.04})
    assert speaker_design.speaker_side(b"speaker") == "left"

    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"yaw": 0.0})
    assert speaker_design.speaker_side(b"speaker") == "left"


def test_plan_layout_prefers_inset_then_side():
    assert speaker_design.plan_layout([object()], [object()]) == "inset"
    assert speaker_design.plan_layout([], [object()]) == "side"
    assert speaker_design.plan_layout([], []) is None


def test_compose_crops_top_then_resizes_without_stretch(monkeypatch):
    source = image_bytes((1024, 1536))
    calls = []
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="speaker")])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="moment")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *args: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.8)

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return source

    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(generate))
    result = speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc wins after a battle", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )

    assert len(calls) == 1
    assert Image.open(BytesIO(result)).size == (1080, 1350)


def test_finish_crops_largest_top_4x5_box_without_stretch():
    source = BytesIO()
    image = Image.new("RGB", (1000, 1600), "blue")
    # The top crop is 1000x1250, so this red marker must survive.
    for x in range(1000):
        for y in range(1240, 1250):
            image.putpixel((x, y), (255, 0, 0))
    image.save(source, format="PNG")
    result = Image.open(BytesIO(speaker_design._finish(source.getvalue())))
    assert result.size == (1080, 1350)
    assert result.getpixel((540, 1345))[0] > 200


def test_detect_faces_returns_scaled_faces_with_raw():
    fake_row = np.array([
        10.0, 20.0, 30.0, 40.0,  # box
        11.0, 21.0, 12.0, 22.0, 13.0, 23.0, 14.0, 24.0, 15.0, 25.0,  # 5 landmarks (10 values)
        0.95,  # score
    ], dtype=np.float32)

    class FakeDetector:
        def setInputSize(self, size): pass
        def detect(self, img):
            # return tuple (1, np.array([[...]]))
            return 1, np.array([fake_row], dtype=np.float32)

    image_data = image_bytes((1280, 720))  # max dimension 1280, scale = 640 / 1280 = 0.5
    from unittest.mock import patch
    with patch("app.services.design_images._get_yunet_detector", return_value=FakeDetector()):
        faces = speaker_design._detect_faces(image_data)

    assert len(faces) == 1
    face = faces[0]
    # Coordinates divided back by scale (0.5), so multiplied by 2
    assert face["box"] == (20.0, 40.0, 60.0, 80.0)
    assert len(face["landmarks"]) == 5
    assert face["raw"] is not None
    assert len(face["raw"]) == 15
    assert np.isclose(face["raw"][0], 20.0)
    assert np.isclose(face["raw"][14], 0.95)


def test_detect_faces_two_faces():
    fake_row1 = np.array([10.0, 20.0, 30.0, 40.0] + [1.0] * 10 + [0.9], dtype=np.float32)
    fake_row2 = np.array([50.0, 60.0, 70.0, 80.0] + [2.0] * 10 + [0.85], dtype=np.float32)

    class FakeDetector:
        def setInputSize(self, size): pass
        def detect(self, img):
            return 2, np.array([fake_row1, fake_row2], dtype=np.float32)

    image_data = image_bytes((640, 640))  # scale = 1.0
    from unittest.mock import patch
    with patch("app.services.design_images._get_yunet_detector", return_value=FakeDetector()):
        faces = speaker_design._detect_faces(image_data)

    assert len(faces) == 2
    assert faces[0]["box"] == (10.0, 20.0, 30.0, 40.0)
    assert faces[1]["box"] == (50.0, 60.0, 70.0, 80.0)


def test_detect_faces_detector_unavailable():
    from unittest.mock import patch
    with patch("app.services.design_images._get_yunet_detector", return_value=None):
        assert speaker_design._detect_faces(image_bytes()) == []


def test_real_photos_face_info_and_identity_score():
    photos_dir = os.environ.get("SPEAKER_TEST_PHOTOS_DIR")
    if not photos_dir or not os.path.isdir(photos_dir):
        pytest.skip("SPEAKER_TEST_PHOTOS_DIR not set or does not exist")

    p48857 = Path(photos_dir) / "48857.jpg"
    p19460 = Path(photos_dir) / "19460.jpg"
    if not p48857.exists() or not p19460.exists():
        pytest.skip("Test photos 48857.jpg or 19460.jpg missing")

    from app.services import design_images
    if design_images._get_yunet_detector() is None or speaker_design._get_sface_model() is None:
        pytest.skip("YuNet or SFace model missing")

    b48857 = p48857.read_bytes()
    b19460 = p19460.read_bytes()

    info1 = speaker_design.face_info(b48857)
    info2 = speaker_design.face_info(b19460)
    assert info1 is not None, "face_info for 48857.jpg must not be None"
    assert info2 is not None, "face_info for 19460.jpg must not be None"

    same_score = speaker_design.identity_score(b48857, b48857)
    diff_score = speaker_design.identity_score(b48857, b19460)
    print(f"\nReal identity score same (48857 vs 48857): {same_score:.4f}")
    print(f"Real identity score diff (48857 vs 19460): {diff_score:.4f}")

    assert same_score > 0.9, f"Expected same identity score > 0.9, got {same_score}"
    assert diff_score < 0.40, f"Expected diff identity score < 0.40, got {diff_score}"


def test_usable_face_checks_quality_dict_and_calls_face_info_once(monkeypatch):
    row = SimpleNamespace(
        label="face", attributes={"quality": {"blurry": True, "obstructed": False}},
        local_path="speaker",
    )
    calls = []
    monkeypatch.setattr(speaker_design, "_read_photo", lambda _: b"photo")
    monkeypatch.setattr(speaker_design, "face_info", lambda _: calls.append(1) or {"box": (0, 0, 200, 200)})
    assert not speaker_design._usable_face(row, ["Marc"])
    assert calls == []

    row.attributes["quality"] = {"blurry": False, "obstructed": False}
    assert speaker_design._usable_face(row, ["Marc"])
    assert calls == [1]


def test_story_mood_detection():
    # serious cases
    assert speaker_design.story_mood("fall in pre-qualifying, rear brake") == "serious"
    assert speaker_design.story_mood("Bastianini suffered painful injury and crash") == "serious"
    # happy cases
    assert speaker_design.story_mood("won the race, so happy") == "happy"
    assert speaker_design.story_mood("fantastic victory champion") == "happy"
    # serious wins over happy if both match
    assert speaker_design.story_mood("great battle but suffered a crash and retired") == "serious"
    # any
    assert speaker_design.story_mood("speaking on thursday conference") == "any"


def test_pick_speaker_photos_ranks_by_expression_mood(monkeypatch):
    row_smiling = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body", "expression": "smiling"},
        local_path="smile", downloaded_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    row_serious = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body", "expression": "serious"},
        local_path="serious", downloaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    row_missing = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body"},
        local_path="missing", downloaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [row_smiling, row_serious, row_missing])
    monkeypatch.setattr(speaker_design, "_usable_face", lambda *_: True)
    monkeypatch.setattr(speaker_design, "_is_half_body_source", lambda *_: True)

    # Serious mood: serious (GOOD) > missing (middle) > smiling (BAD)
    picked_serious = speaker_design.pick_speaker_photos(object(), "Enea Bastianini", mood="serious")
    assert picked_serious[0] is row_serious
    assert picked_serious[1] is row_missing

    # Happy mood: smiling (GOOD) > missing (middle) > serious (neither good nor bad? wait, serious is not in bad for happy: angry, disappointed are bad)
    row_angry = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body", "expression": "angry"},
        local_path="angry", downloaded_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [row_angry, row_missing, row_smiling])
    picked_happy = speaker_design.pick_speaker_photos(object(), "Enea Bastianini", mood="happy")
    assert picked_happy[0] is row_smiling
    assert picked_happy[1] is row_missing


def test_serious_mood_ranks_neutral_podium_photo_as_bad_against_garage_photo(monkeypatch):
    monkeypatch.setattr(speaker_design, "_usable_face", lambda *_: True)
    monkeypatch.setattr(speaker_design, "_is_half_body_source", lambda *_: True)
    # Real incident: Bezzecchi crash story picked a podium photo labeled neutral
    podium_neutral = SimpleNamespace(
        label="face",
        attributes={"shot_type": "half_body", "expression": "neutral", "caption": "standing on podium celebration"},
        source_image_url="https://x/bezzecchi-podium.jpg",
        local_path="podium.jpg",
        downloaded_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    garage_neutral = SimpleNamespace(
        label="face",
        attributes={"shot_type": "half_body", "expression": "neutral", "caption": "sitting in garage"},
        source_image_url="https://x/bezzecchi-garage.jpg",
        local_path="garage.jpg",
        downloaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [podium_neutral, garage_neutral])
    picked = speaker_design.pick_speaker_photos(object(), "Marco Bezzecchi", mood="serious")
    assert picked[0] is garage_neutral


def test_prompt_expression_edit_line_when_best_photo_is_bad():
    photo_smile = SimpleNamespace(
        attributes={"expression": "smiling"},
        local_path="smile",
    )
    # serious mood with smiling photo -> adds serious edit line
    prompt_serious = speaker_design._build_prompt(
        "inset", "Enea Bastianini", "left", "MotoGP", [], [],
        speaker_photo=photo_smile, mood="serious",
    )
    assert "Change ONLY the speaker's facial expression to a calm, serious, focused look (mouth closed, no smile, no teeth showing)." in prompt_serious
    assert "This expression change is the only allowed exception to the rule 'do not change any face'." in prompt_serious

    # happy mood with angry photo -> adds happy edit line
    photo_angry = SimpleNamespace(
        attributes={"expression": "angry"},
        local_path="angry",
    )
    prompt_happy = speaker_design._build_prompt(
        "inset", "Enea Bastianini", "left", "MotoGP", [], [],
        speaker_photo=photo_angry, mood="happy",
    )
    assert "Change ONLY the speaker's facial expression to a natural, subtle smile." in prompt_happy
    assert "This expression change is the only allowed exception to the rule 'do not change any face'." in prompt_happy

    # serious mood with serious photo -> NO edit line
    photo_serious = SimpleNamespace(
        attributes={"expression": "serious"},
        local_path="serious",
    )
    prompt_ok = speaker_design._build_prompt(
        "inset", "Enea Bastianini", "left", "MotoGP", [], [],
        speaker_photo=photo_serious, mood="serious",
    )
    assert "Change ONLY the speaker's facial expression" not in prompt_ok


def test_speaker_framing_with_half_body_row():
    row_half = SimpleNamespace(attributes={"shot_type": "half_body"})
    # Even if image bytes has no face or different framing, half_body shot_type row returns HALF_BODY_FRAMING
    assert speaker_design.speaker_framing(image_bytes(), row=row_half) == speaker_design.HALF_BODY_FRAMING


def test_compose_passes_mood_and_calls_generate_once(monkeypatch):
    source = image_bytes()
    photo_smiling = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body", "expression": "smiling"},
        local_path="smile", downloaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    calls = []
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda db, name, mood="any": calls.append(("pick", mood)) or [photo_smiling])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="moment")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *args: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.8)

    generate_calls = []
    def fake_generate(db, req, **kwargs):
        generate_calls.append(req)
        return source
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(fake_generate))

    result = speaker_design.compose_speaker_design(
        object(), object(), speaker="Enea Bastianini", subjects=["Pecco Bagnaia"],
        article_text="Bastianini suffered a heavy crash and pain", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )
    assert result is not None
    assert len(generate_calls) == 1
    assert calls[0] == ("pick", "serious")
    assert "Change ONLY the speaker's facial expression to a calm, serious, focused look" in generate_calls[0].prompt


def test_compose_speaker_design_story_photo_single(monkeypatch):
    source = image_bytes()
    speaker_row = SimpleNamespace(label="face", attributes={"shot_type": "half_body"}, local_path="speaker.jpg")
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [speaker_row])
    # moment photo does not match event (story is crash, moment is win/podium) -> only story photo used
    moment_row = SimpleNamespace(
        label="action",
        attributes={"shot_type": "podium", "caption": "victory celebration"},
        local_path="moment.jpg",
    )
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [moment_row])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda r: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)

    generate_requests = []
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda db, req, **k: generate_requests.append(req) or source))

    result = speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc crashed out of the lead", sport="MotoGP",
        around=datetime.now(timezone.utc),
        story_photo_path="/tmp/story_photo.jpg",
        story_photo_desc="Marc on gravel trap after crash",
    )
    assert result is not None
    assert len(generate_requests) == 1
    req = generate_requests[0]
    assert req.layout == "inset"
    assert req.refs[0] == "speaker.jpg"
    assert req.refs[1] == "/tmp/story_photo.jpg"
    assert len(req.refs) == 2
    assert "INSET 1: REAL PHOTO of Marc on gravel trap after crash" in req.prompt
    assert "ONE large circle" in req.prompt


def test_compose_speaker_design_story_photo_with_matching_moment(monkeypatch):
    source = image_bytes()
    speaker_row = SimpleNamespace(label="face", attributes={"shot_type": "half_body"}, local_path="speaker.jpg")
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [speaker_row])
    # moment photo matches crash event and has different path
    moment_row = SimpleNamespace(
        label="action",
        attributes={"shot_type": "action", "caption": "crash in turn 4"},
        local_path="/tmp/gallery_crash.jpg",
    )
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [moment_row])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda r: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)

    generate_requests = []
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda db, req, **k: generate_requests.append(req) or source))

    result = speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="Marc crashed out of the lead", sport="MotoGP",
        around=datetime.now(timezone.utc),
        story_photo_path="/tmp/story_photo.jpg",
        story_photo_desc="Marc on gravel trap after crash",
    )
    assert result is not None
    assert len(generate_requests) == 1
    req = generate_requests[0]
    assert req.layout == "inset"
    assert req.refs == ("speaker.jpg", "/tmp/story_photo.jpg", "/tmp/gallery_crash.jpg")
    assert "TWO circles" in req.prompt
    assert "INSET 1: REAL PHOTO of Marc on gravel trap after crash" in req.prompt
    assert "INSET 2: REAL PHOTO of crash in turn 4" in req.prompt


def test_compose_speaker_design_story_photo_none_unchanged(monkeypatch):
    source = image_bytes()
    speaker_row = SimpleNamespace(label="face", attributes={"shot_type": "half_body"}, local_path="speaker.jpg")
    bike_row = SimpleNamespace(label="action", attributes={"shot_type": "car_on_track", "caption": "#93 Marc"}, local_path="bike.jpg")
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [speaker_row])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [bike_row])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda r: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)

    generate_requests = []
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda db, req, **k: generate_requests.append(req) or source))

    result = speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=["Marc Marquez"],
        article_text="Marc won the championship", sport="MotoGP",
        around=datetime.now(timezone.utc),
        story_photo_path=None,
        story_photo_desc=None,
    )
    assert result is not None
    assert len(generate_requests) == 1
    req = generate_requests[0]
    # No moment photos and story_photo_path is None -> layout is side
    assert req.layout == "action"
    assert req.refs[0] == "speaker.jpg"
    assert req.refs[1] == "bike.jpg"


def test_compose_speaker_design_exposes_used_gallery_images(monkeypatch):
    source = image_bytes()
    from app.models.gallery import GalleryImage

    speaker_row = GalleryImage(id=101, label="face", attributes={"shot_type": "half_body"}, local_path="speaker.jpg")
    moment_row = GalleryImage(id=202, label="action", attributes={"shot_type": "action", "caption": "battle"}, local_path="moment.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [speaker_row])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [moment_row])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda r: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda db, req, **k: source))

    res = speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=["Pecco Bagnaia"],
        article_text="great battle on track", sport="MotoGP",
        around=datetime.now(timezone.utc),
    )
    assert res is not None
    assert isinstance(res, bytes)
    assert hasattr(res, "used_gallery_images")
    assert speaker_row in res.used_gallery_images
    assert moment_row in res.used_gallery_images


def test_face_detection_cached_per_image_bytes(monkeypatch):
    raw_calls = []
    fake_face = [{"box": (10, 20, 30, 40), "landmarks": [(1, 1)] * 5, "yaw": 0.0, "raw": [0.0] * 15}]

    def fake_detect_raw(data):
        raw_calls.append(data)
        return fake_face

    monkeypatch.setattr(speaker_design, "_detect_faces_raw", fake_detect_raw)
    speaker_design._face_cache.clear()

    img_data = b"unique-image-bytes-1"
    # Call face_info multiple times on same bytes
    for _ in range(5):
        res = speaker_design.face_info(img_data)
        assert res is not None

    # Only 1 actual detector call was made
    assert len(raw_calls) == 1

    # Different bytes triggers a 2nd detector call
    speaker_design.face_info(b"unique-image-bytes-2")
    assert len(raw_calls) == 2


def test_story_event_detection():
    assert speaker_design.story_event("Bezzecchi crashed in turn 1 after a clash") == "crash"
    assert speaker_design.story_event("Marquez won the race and celebrated victory on podium") == "win"
    assert speaker_design.story_event("Hard battle and overtook on the final lap") == "battle"
    assert speaker_design.story_event("Speaking at the press conference on Thursday") == "general"


def test_photo_event_detection():
    # shot_type podium counts as win
    row_podium = SimpleNamespace(attributes={"shot_type": "podium", "caption": "standing on stage"})
    assert speaker_design.photo_event(row_podium) == "win"

    # caption / getty_slug / source_image_url keywords
    row_crash = SimpleNamespace(attributes={"caption": "rider fell at turn 4"})
    assert speaker_design.photo_event(row_crash) == "crash"

    row_battle = SimpleNamespace(
        attributes={"caption": "normal practice", "getty_slug": "motogp-duel-and-fight"},
        source_image_url="https://x/img.jpg",
    )
    assert speaker_design.photo_event(row_battle) == "battle"

    row_url = SimpleNamespace(
        attributes={},
        source_image_url="https://x/marco-bezzecchi-trophy-celebration.jpg",
    )
    assert speaker_design.photo_event(row_url) == "win"

    row_gen = SimpleNamespace(attributes={"caption": "sitting in garage"})
    assert speaker_design.photo_event(row_gen) == "general"


def test_pick_moment_photos_avoids_contradiction_and_prioritizes_story_event(monkeypatch):
    now = datetime.now(timezone.utc)
    crash_photo = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "action", "caption": "Bezzecchi crash turn 1"},
        source_image_url="https://x/crash.jpg", downloaded_at=now, captured_at=None,
    )
    win_photo = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "podium", "caption": "Bezzecchi trophy celebration win"},
        source_image_url="https://x/podium.jpg", downloaded_at=now + timedelta(minutes=1), captured_at=None,
    )
    general_photo = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "action", "caption": "Bezzecchi on track"},
        source_image_url="https://x/gen.jpg", downloaded_at=now - timedelta(minutes=1), captured_at=None,
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [win_photo, crash_photo, general_photo])

    # Story is crash: win photo is a contradiction and must never be picked; crash photo first
    picked = speaker_design.pick_moment_photos(
        object(), ["Bezzecchi"], now,
        article_text="Bezzecchi suffered a heavy crash", speaker="Bezzecchi",
    )
    assert win_photo not in picked
    assert picked[0] is crash_photo


def test_pick_moment_photos_crash_fallback_to_rival_action(monkeypatch):
    now = datetime.now(timezone.utc)
    win_photo = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "podium", "caption": "Bezzecchi trophy"},
        source_image_url="https://x/podium.jpg", downloaded_at=now, captured_at=None,
    )
    rival_action = SimpleNamespace(
        keyword="Raul Fernandez", extra_keywords=[],
        attributes={"shot_type": "action", "caption": "Raul on track"},
        source_image_url="https://x/raul.jpg", downloaded_at=now - timedelta(minutes=1), captured_at=None,
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [win_photo, rival_action])

    # Story is crash, no crash photo exists: fallback to rival action photo (never win photo)
    picked = speaker_design.pick_moment_photos(
        object(), ["Bezzecchi", "Raul Fernandez"], now,
        article_text="Bezzecchi clash with Raul", speaker="Bezzecchi",
    )
    assert picked == [rival_action]


def test_pick_moment_photos_crash_fallback_to_speaker_action_when_no_rival(monkeypatch):
    now = datetime.now(timezone.utc)
    win_photo = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "podium", "caption": "Bezzecchi trophy"},
        source_image_url="https://x/podium.jpg", downloaded_at=now, captured_at=None,
    )
    speaker_action = SimpleNamespace(
        keyword="Bezzecchi", extra_keywords=[],
        attributes={"shot_type": "action", "caption": "Bezzecchi on track practice"},
        source_image_url="https://x/bez-action.jpg", downloaded_at=now - timedelta(minutes=2), captured_at=None,
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [win_photo, speaker_action])

    # Story is crash, no crash photo, no rival photo -> return newest non-win action photo of speaker
    picked = speaker_design.pick_moment_photos(
        object(), ["Bezzecchi"], now,
        article_text="Bezzecchi crash in FP1", speaker="Bezzecchi",
    )
    assert picked == [speaker_action]


def test_pick_speaker_photos_prioritizes_half_body(monkeypatch):
    source_half = image_bytes((1000, 1000), color=(10, 20, 30))
    source_close = image_bytes((1000, 1000), color=(40, 50, 60))

    row_closeup = SimpleNamespace(
        label="face", attributes={"shot_type": "portrait"},
        local_path="close", downloaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    row_half = SimpleNamespace(
        label="face", attributes={"shot_type": "half_body"},
        local_path="half", downloaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    row_detected_half = SimpleNamespace(
        label="face", attributes={"shot_type": "portrait"},
        local_path="detected_half", downloaded_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [row_closeup, row_half, row_detected_half])
    monkeypatch.setattr(speaker_design, "_usable_face", lambda *_: True)
    # Mock face_info and image size via PIL for detected_half
    def mock_read(row):
        return source_half if row is row_detected_half else source_close
    monkeypatch.setattr(speaker_design, "_read_photo", mock_read)

    def mock_face_info(data):
        if data == source_half:
            # face height (200) / image height (1000) = 0.20 <= 0.22, center y = 100 + 100 = 200 / 1000 = 0.20 <= 0.45
            return {"box": (100, 100, 200, 200)}
        # close-up: face height 400 / 1000 = 0.40
        return {"box": (100, 100, 400, 400)}
    monkeypatch.setattr(speaker_design, "face_info", mock_face_info)

    picked = speaker_design.pick_speaker_photos(object(), "Marc Marquez")
    # Both row_detected_half and row_half are half-body. row_detected_half is newer.
    assert picked[0] is row_detected_half
    assert picked[1] is row_half


def test_speaker_framing_text(monkeypatch):
    source = image_bytes((1000, 1000))
    # Half-body
    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"box": (100, 100, 200, 200)})
    text = speaker_design.speaker_framing(source)
    assert text == speaker_design.HALF_BODY_FRAMING
    assert "face about 34-38 percent of the card width" in text
    assert "the body continues down to the bottom edge" in text

    # Close-up
    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"box": (100, 100, 400, 400)})
    text_close = speaker_design.speaker_framing(source)
    assert "Tight head-and-shoulders" in text_close
    assert "shoulders and chest continue down to the bottom edge" in text_close


def test_composition_band_line_present():
    expected_line = "Keep every face, the inset circle and any important detail ABOVE the lower 37 percent band of the card; the bottom 37 percent may hold only the speaker's body/torso and background, because the headline text is placed there."
    assert expected_line in speaker_design.COMMON


def test_pick_bike_photos_race_number_priority(monkeypatch):
    bike_without_num = SimpleNamespace(
        label="action", attributes={"shot_type": "action", "caption": "Ducati bike on track"},
    )
    bike_with_num = SimpleNamespace(
        label="action", attributes={"shot_type": "action", "caption": "Pecco riding bike #63 on track"},
    )
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: [bike_without_num, bike_with_num])

    picked = speaker_design.pick_bike_photos(object(), "Pecco Bagnaia")
    assert picked[0] is bike_with_num
    assert picked[1] is bike_without_num


def test_moment_ranking_uses_article_words_and_speaker(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(keyword="Marc Marquez", extra_keywords=[], attributes={"shot_type": "action", "caption": "Marc celebrates podium"}, captured_at=None, downloaded_at=now),
        SimpleNamespace(keyword="Marc Marquez", extra_keywords=[], attributes={"shot_type": "action", "caption": "Track practice"}, captured_at=None, downloaded_at=now - timedelta(minutes=1)),
    ]
    monkeypatch.setattr(speaker_design, "_rows", lambda *_: rows)
    picked = speaker_design.pick_moment_photos(
        object(), ["Pecco Bagnaia", "Marc Marquez"], now,
        article_text="Marc celebrates after podium win", speaker="Marc Marquez",
    )
    assert picked[0] is rows[0]
    assert len(picked) == 1


def test_prompt_uses_actual_photo_descriptions_and_reference_layout_wording():
    moment_one = SimpleNamespace(
        attributes={"caption": "Marc celebrates the heat win"},
        source_image_url="https://x/unused.jpg", local_path="one",
    )
    moment_two = SimpleNamespace(
        attributes={}, source_image_url="https://x/pecco-podium-2026.jpg", local_path="two",
    )
    prompt = speaker_design._build_prompt(
        "inset", "Marc Marquez", "right", "MotoGP", [moment_one, moment_two], [],
    )
    assert "top of the head about 8-12 percent from the top edge" in prompt
    assert "face about 40-45 percent of the card width" in prompt
    assert "eyes in the upper third" in prompt
    assert "Marc celebrates the heat win" in prompt
    assert "pecco podium 2026" in prompt
    assert "TWO circles of different sizes" in prompt
    assert "real background from Image 1, extended to fill the card" in prompt
    assert "The light on the speaker's face matches Image 1" in prompt


def test_side_prompt_contains_depth_and_race_number():
    bike = SimpleNamespace(
        attributes={"caption": "Pecco Bagnaia on bike #63"},
        source_image_url="https://x/pecco.jpg", local_path="bike",
    )
    prompt = speaker_design._build_prompt("side", "Marc Marquez", "left", "MotoGP", [], [bike])
    assert "in FRONT of everything" in prompt
    assert "Use this photo AS IS as the back layer" in prompt
    assert "The speaker's shoulder partly covers the near edge of the bike; the bike never covers the speaker" in prompt
    assert "race number 63" in prompt
    assert "Image 3 = a second real photo of the speaker" in prompt


def test_identity_score_matches_best_output_face(monkeypatch):
    class Model:
        def alignCrop(self, image, face): return face
        def feature(self, crop): return crop
        def match(self, ref, out, kind): return out[0]

    monkeypatch.setattr(speaker_design, "_get_sface_model", lambda: Model())
    monkeypatch.setattr(speaker_design, "_detect_faces", lambda data: [
        {"box": (0, 0, 20, 20), "landmarks": [(1, 1)] * 5},
        {"box": (20, 20, 200, 200), "landmarks": [(2, 2)] * 5},
    ])
    # The second output face is the matching one; the first is deliberately not.
    face_calls = []
    monkeypatch.setattr(speaker_design, "face_info", lambda _: {"box": (0, 0, 100, 100), "landmarks": [(1, 1)] * 5})
    monkeypatch.setattr(speaker_design, "_decode", lambda _: object())
    monkeypatch.setattr(speaker_design, "_face_feature_score", lambda model, reference, image, face: face["landmarks"][0][0] / 2)
    assert speaker_design.identity_score(b"ref", b"out") == 1.0


def test_identity_failure_returns_none_and_generate_once(monkeypatch):
    source = image_bytes()
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="speaker")])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="moment")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *args: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.1)
    generated = []
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda *a, **k: generated.append(1) or source))

    assert speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=[], article_text="win",
        sport="MotoGP", around=datetime.now(timezone.utc),
    ) is None
    assert generated == [1]


def test_prompt_copies_reference_wording(monkeypatch):
    source = image_bytes()
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="speaker")])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="moment")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *args: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "right")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.8)
    captured = {}

    def generate(db, request, **kwargs):
        captured["request"] = request
        return source

    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(generate))
    speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=[], article_text="podium",
        sport="MotoGP", around=datetime.now(timezone.utc),
    )
    prompt = captured["request"].prompt
    assert "white ring" in prompt
    assert "AS IS" in prompt
    assert "NO golden hour" in prompt


def test_generation_failure_returns_none(monkeypatch):
    from app.services.visual_engine import VisualEngineError

    source = image_bytes()
    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="speaker")])
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *args, **kwargs: [SimpleNamespace(local_path="moment")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *args: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: source)
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", _as_result(lambda *a, **k: (_ for _ in ()).throw(VisualEngineError("quota", "cap"))))

    assert speaker_design.compose_speaker_design(
        object(), object(), speaker="Marc Marquez", subjects=[], article_text="win",
        sport="MotoGP", around=datetime.now(timezone.utc),
    ) is None
