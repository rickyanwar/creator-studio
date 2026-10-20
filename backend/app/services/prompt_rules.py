TEMPLATE_TEXT_ZONE_PCT = 35

REALISM_LINES = (
    "Photographic realism (very important):",
    "- It must look like a real photograph by a professional sports photographer: realistic skin with pores and natural imperfections, real lens depth of field and motion, natural light and colour.",
    "- Keep it natural: no over-sharpening, no halos around edges, no HDR clarity, no hyper-detailed or crunchy AI texture, no plastic gloss, no airbrushed or waxy skin, no AI sheen. The background stays naturally soft from depth of field, and noise and grain stay as in a real photo.",
)


def realism_block() -> str:
    return "\n".join(REALISM_LINES)


def text_zone_line() -> str:
    return (
        f"The bottom {TEMPLATE_TEXT_ZONE_PCT}% will be covered later by the fan page's badge and headline text, "
        "so keep it simple and calm (a plain background or soft out-of-focus scenery) with no faces, no text and no important detail; it does not need to be dark."
    )


SERIES_LOGO_LINE = (
    "- No official series logo or wordmark anywhere in the image: not on banners, screens, walls, "
    "the track, the octagon mat or fence, or podium backdrops (for example F1, Formula 1, "
    "FIA, MotoGP, FIM, Dorna, UFC). Use plain unbranded surfaces there. A small series logo that is "
    "part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine. Team, manufacturer "
    "and sponsor logos (Ferrari, Red Bull, Ducati, Mercedes, Monster and so on) are fine and must stay exactly as real."
)


def avoid_block(extra: list[str] | tuple[str, ...] = (), *, text_zone: bool = True) -> str:
    lines = [
        "Do NOT include anything below (very important):",
        SERIES_LOGO_LINE,
    ]
    if text_zone:
        lines.append(
            f"- Any text, letters, numbers or graphic words in the bottom {TEMPLATE_TEXT_ZONE_PCT}% of the image."
        )
    lines.append("- Another series' cars, bikes or logos.")
    for item in extra:
        clean = item.strip()
        if not clean:
            continue
        clean = clean.rstrip(".")
        lines.append(f"- {clean}.")
    return "\n".join(lines)
