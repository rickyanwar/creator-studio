#!/usr/bin/env python3
"""Report differences between old per-mode/per-source caption fields and global caption fields.

Read-only script for owner review.
Usage:
    python backend/scripts/dev/report_dropped_caption_overrides.py [DATABASE_URL]
"""
import os
import sys

from sqlalchemy import create_engine, text


def main():
    db_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not provided as argument or environment variable.", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url)
    with engine.connect() as conn:
        fanpages = conn.execute(text("SELECT * FROM target_fanpages ORDER BY id ASC")).mappings().all()

        print("=" * 80)
        print("REPORT: DROPPED CAPTION OVERRIDES vs GLOBAL SETTINGS")
        print("=" * 80)

        for fp in fanpages:
            print(f"\nFanpage ID {fp['id']}: {fp['name']} (Language: {fp['caption_language']})")
            print("-" * 60)

            diffs = []

            # 1. Mode 2 Differences
            m2_diffs = []
            if fp.get("mode2_caption_tone") and fp["mode2_caption_tone"] != fp.get("caption_tone"):
                m2_diffs.append(f"tone: global={fp.get('caption_tone')!r} vs mode2={fp['mode2_caption_tone']!r}")
            if fp.get("mode2_caption_max_length") and fp["mode2_caption_max_length"] != fp.get("caption_max_length"):
                m2_diffs.append(f"max_length: global={fp.get('caption_max_length')} vs mode2={fp['mode2_caption_max_length']}")
            if fp.get("mode2_caption_hashtag_count") and fp["mode2_caption_hashtag_count"] != fp.get("caption_hashtag_count"):
                m2_diffs.append(f"hashtag_count: global={fp.get('caption_hashtag_count')} vs mode2={fp['mode2_caption_hashtag_count']}")
            if fp.get("mode2_caption_cta_text") and fp["mode2_caption_cta_text"] != fp.get("caption_cta_text"):
                m2_diffs.append(f"cta: global={fp.get('caption_cta_text')!r} vs mode2={fp['mode2_caption_cta_text']!r}")
            if fp.get("mode2_source_attribution") is not None and fp["mode2_source_attribution"] != fp.get("use_attribution"):
                m2_diffs.append(f"attribution: global={fp.get('use_attribution')} vs mode2={fp['mode2_source_attribution']}")

            if m2_diffs:
                print("  [Mode 2 News Overrides Differing]")
                for d in m2_diffs:
                    print(f"    - {d}")

            m2_prompt = (fp.get("mode2_caption_custom_prompt") or "").strip()
            if m2_prompt:
                print("  [Mode 2 Custom Prompt to be Dropped]:")
                for line in m2_prompt.splitlines():
                    print(f"    | {line}")

            # 2. Pinterest Differences
            pin_diffs = []
            if fp.get("pinterest_hashtag_count") and fp["pinterest_hashtag_count"] != fp.get("caption_hashtag_count"):
                pin_diffs.append(f"hashtag_count: global={fp.get('caption_hashtag_count')} vs pinterest={fp['pinterest_hashtag_count']}")

            if pin_diffs:
                print("  [Mode 5 Pinterest Overrides Differing]")
                for d in pin_diffs:
                    print(f"    - {d}")

            pin_prompt = (fp.get("pinterest_custom_prompt") or "").strip()
            if pin_prompt:
                print("  [Pinterest Custom Prompt to be Dropped]:")
                for line in pin_prompt.splitlines():
                    print(f"    | {line}")

            # 3. News Sources linked to this fanpage
            ns_rows = conn.execute(
                text(
                    "SELECT ns.* FROM news_sources ns "
                    "JOIN fanpage_news_sources fns ON fns.news_source_id = ns.id "
                    "WHERE fns.fanpage_id = :fpid"
                ),
                {"fpid": fp["id"]}
            ).mappings().all()

            for ns in ns_rows:
                ns_diffs = []
                for field in ("caption_tone", "caption_max_length", "caption_hashtag_count", "caption_cta_text"):
                    val = ns.get(field)
                    glob_val = fp.get(field)
                    if val is not None and str(val).strip() != "" and val != glob_val:
                        ns_diffs.append(f"{field}: global={glob_val!r} vs source={val!r}")
                prompt = (ns.get("caption_custom_prompt") or "").strip()
                if ns_diffs or prompt:
                    print(f"  [News Source Override: {ns['name']} (ID {ns['id']})]")
                    for d in ns_diffs:
                        print(f"    - {d}")
                    if prompt:
                        print("    [Source Custom Prompt to be Dropped]:")
                        for line in prompt.splitlines():
                            print(f"      | {line}")

            # 4. IG Sources linked to this fanpage
            ig_rows = conn.execute(
                text(
                    "SELECT igs.* FROM ig_sources igs "
                    "JOIN fanpage_sources fs ON fs.ig_source_id = igs.id "
                    "WHERE fs.fanpage_id = :fpid"
                ),
                {"fpid": fp["id"]}
            ).mappings().all()

            for igs in ig_rows:
                ig_diffs = []
                for field in ("caption_tone", "caption_max_length", "caption_hashtag_count", "caption_cta_text"):
                    val = igs.get(field)
                    glob_val = fp.get(field)
                    if val is not None and str(val).strip() != "" and val != glob_val:
                        ig_diffs.append(f"{field}: global={glob_val!r} vs source={val!r}")
                prompt = (igs.get("caption_custom_prompt") or "").strip()
                if ig_diffs or prompt:
                    print(f"  [IG Source Override: @{igs['ig_username']} (ID {igs['id']})]")
                    for d in ig_diffs:
                        print(f"    - {d}")
                    if prompt:
                        print("    [Source Custom Prompt to be Dropped]:")
                        for line in prompt.splitlines():
                            print(f"      | {line}")

            if not m2_diffs and not m2_prompt and not pin_diffs and not pin_prompt and not ns_rows and not ig_rows:
                print("  No differing overrides found.")

        print("\n" + "=" * 80)
        print("END OF REPORT")
        print("=" * 80)


if __name__ == "__main__":
    main()
