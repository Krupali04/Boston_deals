import re

UA = {"User-Agent": "Mozilla/5.0 (compatible; boston-deals-portfolio/0.1; taybos4811@gmail.com)"}

IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{1,30})", re.I)
NOT_HANDLES = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "developer", "legal"}

def extract_ig_handle(text):
    """Pull an Instagram handle out of a URL, an '@handle', or a page's HTML."""
    if not text:
        return None
    if "instagram.com" not in text.lower():
        h = text.strip().lstrip("@")
        return h if re.fullmatch(r"[A-Za-z0-9_.]{1,30}", h) else None
    for h in IG_RE.findall(text):
        h = h.rstrip(".")
        if h.lower() not in NOT_HANDLES:
            return h
    return None