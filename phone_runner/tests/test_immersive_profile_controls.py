"""Tests for measured own Agung immersive profile controls variant."""

import xml.etree.ElementTree as ET
import pytest

from phone_runner.identity_proof import find_own_profile_controls


def build_immersive_profile_xml(
    name: str = "OwnRootUser",
    has_more: bool = True,
    has_cover: bool = True,
    has_profile_pic: bool = True,
    has_name: bool = True,
    has_personal_details: bool = True,
    has_dashboard: bool = True,
    dashboard_label: str = "Dashboard",
    more_bounds: str = "[918,96][1080,228]",
    cover_bounds: str = "[936,522][1068,654]",
    profile_pic_bounds: str = "[654,685][762,793]",
    name_bounds: str = "[396,854][606,936]",
    personal_details_bounds: str = "[984,1884][1044,1947]",
    more_enabled: bool = True,
    more_clickable: bool = True,
    cover_enabled: bool = True,
    cover_clickable: bool = True,
    profile_pic_enabled: bool = True,
    profile_pic_clickable: bool = True,
    name_enabled: bool = True,
    name_clickable: bool = True,
    personal_details_enabled: bool = True,
    personal_details_clickable: bool = True,
    package: str = "com.facebook.katana",
    duplicate_more: bool = False,
    duplicate_cover: bool = False,
    duplicate_name: bool = False,
    identical_profile_pic_duplicate: bool = False,
    distinct_profile_pic_duplicate: bool = False,
    visitor_action: str | None = None,
) -> str:
    nodes = []

    if has_more:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="{str(more_clickable).lower()}" enabled="{str(more_enabled).lower()}" '
            f'bounds="{more_bounds}" content-desc="More profile settings" text="" />'
        )
        if duplicate_more:
            nodes.append(
                f'<node package="{package}" class="android.widget.Button" '
                f'clickable="true" enabled="true" bounds="[918,250][1080,350]" '
                f'content-desc="More profile settings" text="" />'
            )

    if has_cover:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="{str(cover_clickable).lower()}" enabled="{str(cover_enabled).lower()}" '
            f'bounds="{cover_bounds}" content-desc="Edit cover photo" text="" />'
        )
        if duplicate_cover:
            nodes.append(
                f'<node package="{package}" class="android.widget.Button" '
                f'clickable="true" enabled="true" bounds="[936,660][1068,750]" '
                f'content-desc="Edit cover photo" text="" />'
            )

    if has_profile_pic:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="{str(profile_pic_clickable).lower()}" enabled="{str(profile_pic_enabled).lower()}" '
            f'bounds="{profile_pic_bounds}" content-desc="Edit profile picture" text="" />'
        )
        if identical_profile_pic_duplicate:
            # Accessibility duplicate with identical package/class/clickable/enabled/bounds/label
            nodes.append(
                f'<node package="{package}" class="android.widget.Button" '
                f'clickable="{str(profile_pic_clickable).lower()}" enabled="{str(profile_pic_enabled).lower()}" '
                f'bounds="{profile_pic_bounds}" content-desc="Edit profile picture" text="" />'
            )
        if distinct_profile_pic_duplicate:
            # Disallowed duplicate at distinct physical bounds
            nodes.append(
                f'<node package="{package}" class="android.widget.Button" '
                f'clickable="true" enabled="true" bounds="[654,800][762,900]" '
                f'content-desc="Edit profile picture" text="" />'
            )

    if has_name:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="{str(name_clickable).lower()}" enabled="{str(name_enabled).lower()}" '
            f'bounds="{name_bounds}" content-desc="" text="{name}" />'
        )
        if duplicate_name:
            nodes.append(
                f'<node package="{package}" class="android.widget.Button" '
                f'clickable="true" enabled="true" bounds="[396,950][606,1020]" '
                f'content-desc="" text="{name}" />'
            )

    if has_personal_details:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="{str(personal_details_clickable).lower()}" enabled="{str(personal_details_enabled).lower()}" '
            f'bounds="{personal_details_bounds}" content-desc="Edit Personal details" text="" />'
        )

    if has_dashboard:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="true" enabled="true" bounds="[36,1713][530,1821]" '
            f'content-desc="{dashboard_label}" text="{dashboard_label}" />'
        )

    if visitor_action:
        nodes.append(
            f'<node package="{package}" class="android.widget.Button" '
            f'clickable="true" enabled="true" bounds="[100,1200][300,1300]" '
            f'content-desc="{visitor_action}" text="{visitor_action}" />'
        )

    inner = "".join(nodes)
    return f'<hierarchy rotation="0" bounds="[0,0][1080,2408]">{inner}</hierarchy>'


def test_measured_immersive_profile_success():
    xml = build_immersive_profile_xml(name="OwnRootUser")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (918, 96, 1080, 228)


def test_measured_immersive_profile_professional_dashboard_success():
    xml = build_immersive_profile_xml(name="OwnRootUser", dashboard_label="Professional dashboard")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (918, 96, 1080, 228)


def test_measured_immersive_identical_duplicate_photo_editor_success():
    # Accessibility duplicate at identical bounds passes
    xml = build_immersive_profile_xml(name="OwnRootUser", identical_profile_pic_duplicate=True)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (918, 96, 1080, 228)


def test_immersive_distinct_bounds_duplicate_photo_editor_rejected():
    # Distinct bounds duplicate fails
    xml = build_immersive_profile_xml(name="OwnRootUser", distinct_profile_pic_duplicate=True)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_unrelated_sentence_with_dashboard_substring_rejected():
    # An unrelated sentence containing 'dashboard' as a substring must NOT meet immersive marker
    xml = build_immersive_profile_xml(name="OwnRootUser", dashboard_label="View your activity dashboard insights here")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_missing_dashboard_rejected():
    xml = build_immersive_profile_xml(has_dashboard=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_missing_edit_cover_photo_rejected():
    xml = build_immersive_profile_xml(has_cover=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_missing_edit_profile_picture_rejected():
    xml = build_immersive_profile_xml(has_profile_pic=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_missing_edit_personal_details_two_editor_success():
    xml = build_immersive_profile_xml(has_personal_details=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (918, 96, 1080, 228)


def test_immersive_missing_more_rejected():
    xml = build_immersive_profile_xml(has_more=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_disabled_editor_rejected():
    xml = build_immersive_profile_xml(cover_enabled=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_unclickable_editor_rejected():
    xml = build_immersive_profile_xml(profile_pic_clickable=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_disabled_more_rejected():
    xml = build_immersive_profile_xml(more_enabled=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_foreign_package_rejected():
    xml = build_immersive_profile_xml(package="com.other.app")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_wrong_name_rejected():
    xml = build_immersive_profile_xml(name="OtherUser")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_missing_name_rejected():
    xml = build_immersive_profile_xml(has_name=False)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_duplicate_more_button_rejected():
    xml = build_immersive_profile_xml(duplicate_more=True)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_duplicate_cover_editor_rejected():
    xml = build_immersive_profile_xml(duplicate_cover=True)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_duplicate_name_rejected():
    xml = build_immersive_profile_xml(duplicate_name=True)
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_invalid_bounds_rejected():
    # zero height bounds [936,522][1068,522]
    xml = build_immersive_profile_xml(cover_bounds="[936,522][1068,522]")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_wrong_spatial_order_more_not_above_cover_rejected():
    # more below cover
    xml = build_immersive_profile_xml(more_bounds="[918,600][1080,700]", cover_bounds="[936,522][1068,580]")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_wrong_spatial_order_name_not_below_profile_pic_rejected():
    # name above profile picture editor
    xml = build_immersive_profile_xml(profile_pic_bounds="[654,800][762,900]", name_bounds="[396,680][606,750]")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_wrong_spatial_order_personal_details_not_below_name_rejected():
    # personal details above name
    xml = build_immersive_profile_xml(name_bounds="[396,1500][606,1600]", personal_details_bounds="[984,1200][1044,1300]")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_visitor_add_friend_rejected():
    xml = build_immersive_profile_xml(visitor_action="Add Friend")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_visitor_message_rejected():
    xml = build_immersive_profile_xml(visitor_action="Message")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_immersive_visitor_follow_rejected():
    xml = build_immersive_profile_xml(visitor_action="Follow")
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert more_bounds is None


def test_classic_legacy_profile_controls_pass_unchanged():
    classic_xml = (
        '<hierarchy rotation="0">'
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnRootUser" content-desc="" />'
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" />'
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" />'
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" />'
        '</hierarchy>'
    )
    tree = ET.fromstring(classic_xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (924, 266, 1032, 374)
