#!/usr/bin/env python3
"""
Create an instagrapi session from browser cookies.
Get sessionid + ds_user_id from Chrome DevTools → Application → Cookies → instagram.com
"""
import json, uuid, random, string

DS_USER_ID = input("ds_user_id (numbers only): ").strip()
SESSION_ID = input("sessionid (long string with %3A): ").strip()
USERNAME   = input("IG username: ").strip()

def rand_uuid():
    return str(uuid.uuid4())

def rand_android_id():
    return "android-" + "".join(random.choices(string.hexdigits[:16], k=16)).lower()

session = {
    "uuids": {
        "phone_id":          rand_uuid(),
        "uuid":              rand_uuid(),
        "client_session_id": rand_uuid(),
        "advertising_id":    rand_uuid(),
        "android_device_id": rand_android_id(),
        "request_id":        rand_uuid(),
        "tray_session_id":   rand_uuid(),
    },
    "mid": "",
    "ig_u_rur": None,
    "ig_www_claim": None,
    "authorization_data": {
        "ds_user_id": DS_USER_ID,
        "sessionid":  SESSION_ID,
    },
    "cookies": {},
    "last_login": 0,
    "device_settings": {
        "android_version": 26,
        "android_release": "8.0.0",
        "dpi": "480dpi",
        "resolution": "1080x1920",
        "manufacturer": "OnePlus",
        "device": "devitron",
        "model": "6T Dev",
        "cpu": "qcom",
        "app_version": "385.0.0.47.74",
        "version_code": "605972356",
    },
    "user_agent": "Instagram 385.0.0.47.74 Android (26/8.0.0; 480dpi; 1080x1920; OnePlus; 6T Dev; devitron; qcom; en_US; 605972356)",
    "country": "US",
    "country_code": 1,
    "locale": "en_US",
    "timezone_offset": 25200,
    "username": USERNAME,
    "user_id": DS_USER_ID,
}

out = f"session_{USERNAME}.json"
with open(out, "w") as f:
    json.dump(session, f, indent=2)

print(f"\n✅ Session file created: {out}")
print("Now go to Burner Accounts → Import Session → paste the contents of that file.")
