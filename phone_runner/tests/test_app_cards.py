import json
from pathlib import Path


def test_app_cards_json_mapping():
    pkg_root = Path(__file__).parent.parent
    mapping_path = pkg_root / "app_cards" / "android" / "app_cards.json"
    assert mapping_path.is_file(), f"Missing app_cards.json at {mapping_path}"

    with open(mapping_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "com.facebook.katana" in data
    assert data["com.facebook.katana"] == "facebook.md"


def test_facebook_app_card_content():
    pkg_root = Path(__file__).parent.parent
    doc_path = pkg_root / "app_cards" / "android" / "facebook.md"
    assert doc_path.is_file(), f"Missing facebook.md at {doc_path}"

    content = doc_path.read_text(encoding="utf-8")

    assert "Version: 1.0.0" in content or "1.0.0" in content
    assert "com.facebook.katana" in content
    assert "Main Profile" in content or "Profile" in content
    assert "Managed Pages" in content or "Pages" in content
    assert "STOP BEFORE PUBLISHING" in content or "UNDER NO CIRCUMSTANCES" in content
    assert "DO NOT TAP" in content or "never" in content.lower()


def test_mobilerun_config_template():
    pkg_root = Path(__file__).parent.parent
    tmpl_path = pkg_root / "mobilerun_config.yaml.template"
    assert tmpl_path.is_file(), f"Missing config template at {tmpl_path}"

    content = tmpl_path.read_text(encoding="utf-8")
    assert "_version: 7" in content
    assert "app_cards:" in content
    assert "enabled: true" in content
    assert "mode: local" in content
    assert "app_cards_dir:" in content


def test_get_local_app_card_version_real_file():
    from phone_runner.dry_run import get_local_app_card_version

    ver = get_local_app_card_version()
    assert ver == "1.0.0"


def test_get_local_app_card_version_formats(tmp_path):
    from phone_runner.dry_run import get_local_app_card_version

    card1 = tmp_path / "card1.md"
    card1.write_text("# Card\n- **Version**: 2.3.4\n", encoding="utf-8")
    assert get_local_app_card_version(card1) == "2.3.4"

    card2 = tmp_path / "card2.md"
    card2.write_text("# Card\n- **Version:** 2.3.4\n", encoding="utf-8")
    assert get_local_app_card_version(card2) == "2.3.4"

    card3 = tmp_path / "card3.md"
    card3.write_text("# Card\nVersion: 2.3.4\n", encoding="utf-8")
    assert get_local_app_card_version(card3) == "2.3.4"


def test_get_local_app_card_version_missing_and_malformed(tmp_path):
    from phone_runner.dry_run import get_local_app_card_version

    missing = tmp_path / "nonexistent.md"
    assert get_local_app_card_version(missing) is None

    empty = tmp_path / "empty.md"
    empty.write_text("# No version line\n", encoding="utf-8")
    assert get_local_app_card_version(empty) is None

    malformed = tmp_path / "malformed.md"
    malformed.write_text("# Version: invalid-version\n", encoding="utf-8")
    assert get_local_app_card_version(malformed) is None
