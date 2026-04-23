#!/usr/bin/env bash
set -euo pipefail

# Instagram SuperPower -- Analyze Account via HikerAPI
# Usage: bash analyze.sh <username> [top_N] [days]
# Example: bash analyze.sh example_account 5 14

USERNAME="${1:?Usage: analyze.sh <username> [top_N] [days]}"
TOP_N="${2:-5}"
DAYS="${3:-14}"

HIKER_KEY_FILE="$HOME/.secrets/hikerapi/api-key"

if [ ! -f "$HIKER_KEY_FILE" ]; then
    echo "ERROR: HikerAPI key not found at $HIKER_KEY_FILE"
    exit 1
fi

HIKER_KEY=$(cat "$HIKER_KEY_FILE")

echo "Analyzing @${USERNAME} -- top ${TOP_N} reels, last ${DAYS} days..."
echo ""

python3 << PYEOF
import json, sys, urllib.request
from datetime import datetime, timezone, timedelta

username = "${USERNAME}"
top_n = ${TOP_N}
days = ${DAYS}
hiker_key = "${HIKER_KEY}"
base = "https://api.instagrapi.com"

def api_get(endpoint, params={}):
    params_str = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{base}{endpoint}?{params_str}" if params_str else f"{base}{endpoint}"
    req = urllib.request.Request(url, headers={
        "accept": "application/json",
        "x-access-key": hiker_key,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

# 1. Profile
print(f"Looking up @{username}...")
try:
    profile = api_get("/v2/user/by/username", {"username": username})
    user = profile.get("user", profile)
    user_id = user.get("pk") or user.get("id")
    print(f"Account: @{user.get('username', username)}")
    print(f"Full name: {user.get('full_name', '?')}")
    print(f"Followers: {user.get('follower_count', '?'):,}")
    print(f"Following: {user.get('following_count', '?'):,}")
    print(f"Posts: {user.get('media_count', '?'):,}")
    print(f"Verified: {user.get('is_verified', '?')}")
    print(f"Category: {user.get('category', '?')}")
    print(f"Bio: {user.get('biography', '')[:100]}")
    print()
except Exception as e:
    print(f"Profile lookup failed: {e}")
    sys.exit(1)

# 2. Clips
print(f"Fetching reels...")
cutoff = datetime.now(timezone.utc) - timedelta(days=days)
all_reels = []
page_id = None

for page in range(5):  # max 5 pages (~60 reels)
    params = {"user_id": str(user_id)}
    if page_id:
        params["page_id"] = page_id
    
    try:
        data = api_get("/v2/user/clips", params)
    except Exception as e:
        print(f"Clips fetch error: {e}")
        break
    
    items = data.get("response", {}).get("items", [])
    if not items:
        break
    
    oldest_in_page = None
    for item in items:
        media = item.get("media", {})
        taken_at = media.get("taken_at", 0)
        taken_dt = datetime.fromtimestamp(taken_at, tz=timezone.utc)
        oldest_in_page = taken_dt
        
        if taken_dt < cutoff:
            continue
        
        code = media.get("code", "")
        views = media.get("play_count", 0) or media.get("view_count", 0) or 0
        likes = media.get("like_count", 0) or 0
        comments = media.get("comment_count", 0) or 0
        caption = ""
        cap = media.get("caption")
        if isinstance(cap, dict):
            caption = cap.get("text", "")[:200]
        
        all_reels.append({
            "code": code,
            "url": f"https://www.instagram.com/reel/{code}/",
            "date": taken_dt.strftime("%Y-%m-%d"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "caption": caption.replace("\n", " "),
            "engagement": views + likes * 10,
        })
    
    page_id = data.get("next_page_id")
    if not page_id:
        break
    if oldest_in_page and oldest_in_page < cutoff:
        break

# 3. Sort and display
all_reels.sort(key=lambda r: r["engagement"], reverse=True)
top = all_reels[:top_n]

print(f"\nFound {len(all_reels)} reels in last {days} days")
print(f"TOP {len(top)} by engagement:\n")
print("=" * 70)

for i, r in enumerate(top, 1):
    print(f"\n#{i}")
    print(f"  URL: {r['url']}")
    print(f"  Date: {r['date']}")
    print(f"  Views: {r['views']:,}")
    print(f"  Likes: {r['likes']:,}")
    print(f"  Comments: {r['comments']:,}")
    print(f"  Caption: {r['caption'][:120]}")
    print(f"  Engagement: {r['engagement']:,}")

print("\n" + "=" * 70)
print("JSON:")
print(json.dumps(top, ensure_ascii=False, indent=2))

# 4. Balance
try:
    balance = api_get("/sys/balance")
    print(f"\nHikerAPI balance: \${balance['amount']:.2f} ({balance['requests']:,} requests left)")
except:
    pass
PYEOF

echo ""
echo "Done."
