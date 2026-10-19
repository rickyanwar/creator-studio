from app.services.prompt_rules import (
    TEMPLATE_TEXT_ZONE_PCT,
    REALISM_LINES,
    realism_block,
    text_zone_line,
    SERIES_LOGO_LINE,
    avoid_block,
)


def test_constants():
    assert TEMPLATE_TEXT_ZONE_PCT == 42
    assert len(REALISM_LINES) == 4
    assert REALISM_LINES[0].startswith("Photographic realism")


def test_realism_block():
    block = realism_block()
    assert block == "\n".join(REALISM_LINES)
    assert "Photographic realism (very important):" in block
    assert "pores and natural imperfections" in block


def test_text_zone_line():
    line = text_zone_line()
    assert "Bottom 42% MUST be a smooth dark gradient" in line
    assert "(the fan page's badge and headline are added there later)." in line


def test_avoid_block_default():
    block = avoid_block()
    assert block.startswith("Do NOT include anything below (very important):")
    assert SERIES_LOGO_LINE in block
    assert f"bottom {TEMPLATE_TEXT_ZONE_PCT}% of the image." in block
    assert "- Another series' cars, bikes or logos." in block


def test_avoid_block_without_text_zone():
    block = avoid_block(text_zone=False)
    assert block.startswith("Do NOT include anything below (very important):")
    assert SERIES_LOGO_LINE in block
    assert f"bottom {TEMPLATE_TEXT_ZONE_PCT}%" not in block
    assert "- Another series' cars, bikes or logos." in block


def test_avoid_block_extras():
    block = avoid_block(extra=["custom item", "another item."], text_zone=True)
    assert "- custom item." in block
    assert "- another item." in block
    assert ".." not in block
