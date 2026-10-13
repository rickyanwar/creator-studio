from app.tasks.design_renderer import _speaker_line_fields


def test_quote_template_with_caption_only_moves_subtitle_to_caption():
    tpl = {
        "objects": [
            {"type": "textbox", "placeholderRole": "caption"},
        ]
    }
    subtitle, caption = _speaker_line_fields(tpl, "Davide Tardozzi", "")
    assert subtitle == ""
    assert caption == "Davide Tardozzi"


def test_template_with_subtitle_object_keeps_fields_unchanged():
    tpl = {
        "objects": [
            {"type": "textbox", "placeholderRole": "subtitle"},
            {"type": "textbox", "placeholderRole": "caption"},
        ]
    }
    subtitle, caption = _speaker_line_fields(tpl, "Davide Tardozzi", "")
    assert subtitle == "Davide Tardozzi"
    assert caption == ""


def test_non_empty_caption_keeps_fields_unchanged():
    tpl = {
        "objects": [
            {"type": "textbox", "placeholderRole": "caption"},
        ]
    }
    subtitle, caption = _speaker_line_fields(tpl, "Davide Tardozzi", "Existing Caption")
    assert subtitle == "Davide Tardozzi"
    assert caption == "Existing Caption"


def test_empty_subtitle_keeps_fields_unchanged():
    tpl = {
        "objects": [
            {"type": "textbox", "placeholderRole": "caption"},
        ]
    }
    subtitle, caption = _speaker_line_fields(tpl, "", "")
    assert subtitle == ""
    assert caption == ""
