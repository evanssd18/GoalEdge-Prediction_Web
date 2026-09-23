"""Probe the player-photo URL shapes for a known player id.

Establishes which URL actually returns an image, rather than assuming a path
convention. Run with a real player id from a lineups feed.

    .\\.venv\\Scripts\\python.exe ..\\qa\\probe-player-photo.py OK7S1gmO
"""
import sys
import urllib.request

pid = sys.argv[1] if len(sys.argv) > 1 else "OK7S1gmO"

# The shapes worth trying. Flashscore serves player images from a CDN keyed by
# the same player id the lineups feed publishes as LP.
CANDIDATES = [
    f"https://www.flashscore.com/res/image/data/{pid}.png",
    f"https://www.flashscore.com/res/image/player/{pid}.png",
    f"https://static.flashscore.com/res/image/data/{pid}.png",
    f"https://static.flashscore.com/res/image/player/{pid}.png",
    f"https://www.flashscore.com/res/image/data/{pid}.jpg",
    f"https://static.flashscore.com/res/image/data/{pid}.jpg",
    # The 2nd/3rd path segments are commonly the hash pair Flashscore uses.
    f"https://static.flashscore.com/res/image/data/{pid[:2]}/{pid}.png",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

for url in CANDIDATES:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            print(f"{resp.status}  {ctype:24} {len(body):>7}B  {url}")
            # A real image starts with a known magic number. An HTML error page
            # served with a 200 is the trap here.
            if body[:8].startswith(b"\x89PNG"):
                print("     ^^ PNG")
            elif body[:3] == b"\xff\xd8\xff":
                print("     ^^ JPEG")
            elif body[:6] in (b"GIF87a", b"GIF89a"):
                print("     ^^ GIF")
            elif body[:4] == b"RIFF":
                print("     ^^ WEBP")
            else:
                print(f"     ^^ NOT AN IMAGE (first bytes: {body[:16]!r})")
    except Exception as exc:  # noqa: BLE001
        print(f"ERR   {url}  ->  {type(exc).__name__}: {exc}")
