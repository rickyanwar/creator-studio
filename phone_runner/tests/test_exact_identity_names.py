import xml.etree.ElementTree as ET
import pytest
from phone_runner.identity_proof import (
    find_switcher_target_page,
    find_composer_open_trigger,
)


def test_root_own_root_active_with_page_own_root_extra_succeeds():
    """Root OwnRoot active + Page OwnRootExtra unselected + target OwnPage succeeds."""
    xml_data = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" content-desc="OwnRoot. See your profile" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
          <node text="See your profile" content-desc="See your profile" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" content-desc="PageOwnRootExtra. 5 notifications" package="com.facebook.katana">
          <node text="PageOwnRootExtra" content-desc="PageOwnRootExtra" package="com.facebook.katana" />
          <node text="5 notifications" content-desc="5 notifications" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" content-desc="OwnPage. 10 notifications" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
          <node text="10 notifications" content-desc="10 notifications" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_data)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is True
    assert err == ""
    assert bounds == (0, 600, 1080, 700)


def test_root_own_root_and_own_root_space_extra_are_distinct():
    """OwnRoot and OwnRoot Extra are distinct rows; prefix match does not cause ambiguity."""
    xml_data = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" package="com.facebook.katana">
          <node text="OwnRoot Extra" content-desc="OwnRoot Extra" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_data)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is True
    assert err == ""
    assert bounds == (0, 600, 1080, 700)


def test_target_own_page_and_own_page_extra_distinct_picks_actual_target_bounds():
    """Target OwnPage and OwnPageExtra/OwnPage Extra are distinct; picks actual target bounds."""
    xml_data = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,700][1080,800]" package="com.facebook.katana">
          <node text="OwnPage Extra" content-desc="OwnPage Extra" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_data)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is True
    assert err == ""
    assert bounds == (0, 600, 1080, 700)


def test_only_prefix_root_or_target_present_rejects():
    """Only prefix root or prefix target present rejects without substring match."""
    # Prefix root only: OwnRoot Extra is present, but requested root is OwnRoot
    xml_prefix_root = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot Extra" content-desc="OwnRoot Extra" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree_root = ET.fromstring(xml_prefix_root)
    ok, err, bounds = find_switcher_target_page(tree_root, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Root profile 'OwnRoot' not verified" in err
    assert bounds is None

    # Prefix target only: OwnPage Extra is present, but requested target is OwnPage
    xml_prefix_target = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage Extra" content-desc="OwnPage Extra" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree_target = ET.fromstring(xml_prefix_target)
    ok, err, bounds = find_switcher_target_page(tree_target, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Target page 'OwnPage' not found" in err
    assert bounds is None


def test_exact_duplicate_root_including_one_unselected_still_rejects():
    """Exact duplicate root rows reject even when one is unselected."""
    xml_dup_root = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" selected="false" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_dup_root)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Ambiguous switcher rows: found 2 concrete root rows" in err
    assert bounds is None


def test_exact_duplicate_target_rejects():
    """Exact duplicate target rows reject as ambiguous."""
    xml_dup_target = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,700][1080,800]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_dup_target)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Ambiguous switcher rows: found 2 rows matching 'OwnPage'" in err
    assert bounds is None


def test_other_active_root_rejects():
    """Switcher with different active root rejects."""
    xml_other_active = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OtherRoot" content-desc="OtherRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" selected="false" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_other_active)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Different root session is active in switcher" in err
    assert bounds is None


def test_discrete_exact_labels_with_see_your_profile_and_notif_statuses_pass():
    """Discrete exact labels with recognized trailing status pass cleanly."""
    # Dot status
    xml_dot_status = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" content-desc="OwnRoot. See your profile" package="com.facebook.katana">
          <node text="OwnRoot" content-desc="OwnRoot" package="com.facebook.katana" />
          <node text="See your profile" content-desc="See your profile" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" content-desc="OwnPage. 10 notifications" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
          <node text="10 notifications" content-desc="10 notifications" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree_dot = ET.fromstring(xml_dot_status)
    ok, err, bounds = find_switcher_target_page(tree_dot, "OwnPage", "OwnRoot")
    assert ok is True
    assert err == ""
    assert bounds == (0, 600, 1080, 700)

    # Comma status, row desc only without separate child text nodes
    xml_comma_status = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" content-desc="OwnRoot, See your profile" package="com.facebook.katana" />
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" content-desc="OwnPage, 1 notification" package="com.facebook.katana" />
      </node>
    </hierarchy>"""
    tree_comma = ET.fromstring(xml_comma_status)
    ok2, err2, bounds2 = find_switcher_target_page(tree_comma, "OwnPage", "OwnRoot")
    assert ok2 is True
    assert err2 == ""
    assert bounds2 == (0, 600, 1080, 700)


def test_unrelated_sentence_containing_root_rejects():
    """Unrelated sentence containing root name is rejected."""
    xml_sentence = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="Welcome to OwnRoot fan community" content-desc="Welcome to OwnRoot fan community" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPage" content-desc="OwnPage" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_sentence)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPage", "OwnRoot")
    assert ok is False
    assert "Root profile 'OwnRoot' not verified" in err
    assert bounds is None


def test_exact_composer_allowlist_child_anchor_passes():
    """Exact composer allowlist child anchor returns anchor bounds avoiding photo sibling."""
    xml_composer = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,880][1080,1070]" package="com.facebook.katana">
          <node class="android.widget.ImageView" bounds="[36,917][156,1037]" content-desc="Profile picture icon" package="com.facebook.katana" />
          <node class="android.widget.Button" clickable="false" bounds="[156,944][936,1009]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
          <node class="android.widget.Button" clickable="true" bounds="[936,923][1044,1031]" content-desc="Photo" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_composer)
    ok, err, bounds = find_composer_open_trigger(tree)
    assert ok is True
    assert err == ""
    assert bounds == (156, 944, 936, 1009)


def test_clickable_label_create_post_and_publish_now_rejects():
    """Clickable label 'Create post and publish now' is rejected by exact matching."""
    xml_composer = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.widget.Button" clickable="true" bounds="[100,200][500,300]" text="Create post and publish now" content-desc="Create post and publish now" package="com.facebook.katana" />
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_composer)
    ok, err, bounds = find_composer_open_trigger(tree)
    assert ok is False
    assert "Composer open trigger not found on profile" in err
    assert bounds is None


def test_unrelated_sentence_containing_trigger_rejects():
    """Unrelated sentence containing trigger keyword is rejected by exact matching."""
    xml_composer = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.widget.Button" clickable="true" bounds="[100,200][500,300]" text="Tell us what's on your mind today" content-desc="Tell us what's on your mind today" package="com.facebook.katana" />
      </node>
    </hierarchy>"""
    tree = ET.fromstring(xml_composer)
    ok, err, bounds = find_composer_open_trigger(tree)
    assert ok is False
    assert "Composer open trigger not found on profile" in err
    assert bounds is None
