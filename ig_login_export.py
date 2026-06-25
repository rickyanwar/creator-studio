#!/usr/bin/env python3
"""
Run this LOCALLY to generate an instagrapi session for a burner account.
Then paste the session JSON into the app via Burner → Import Session.

Usage:
  pip install instagrapi
  python3 ig_login_export.py
"""
import json
from instagrapi import Client

USERNAME  = input("IG username: ").strip()
PASSWORD  = input("IG password: ").strip()
PROXY     = input("Proxy URL (e.g. http://user:pass@host:port) or press Enter to skip: ").strip()

cl = Client()
if PROXY:
    cl.set_proxy(PROXY)
    print(f"\nUsing proxy: {PROXY}")
else:
    print("\nNo proxy — using your local IP")

print("Logging in…")

def save_session(client, username):
    session = client.get_settings()
    out = f"session_{username}.json"
    with open(out, "w") as f:
        json.dump(session, f, indent=2)
    print(f"\n✅ Session saved to: {out}")
    print("Now go to Burner Accounts → Import Session → paste the contents of that file.")
    return out

try:
    cl.login(USERNAME, PASSWORD)
    save_session(cl, USERNAME)

except Exception as e:
    err = str(e)
    print(f"\n❌ {type(e).__name__}: {err}")

    if "challenge" in err.lower() or "ChallengeRequired" in type(e).__name__:
        print("\nInstagram sent a verification code to your email or phone.")
        code = input("Enter the 6-digit code: ").strip()
        if code:
            try:
                cl.challenge_resolve(code)
                save_session(cl, USERNAME)
            except Exception as e2:
                print(f"❌ OTP failed: {e2}")
    else:
        print("\nTips:")
        print("  • Try without a proxy (press Enter when asked) — use your local IP")
        print("  • Check username/password are correct")
        print("  • If 'can\\'t find account' with proxy → try a different proxy IP")
