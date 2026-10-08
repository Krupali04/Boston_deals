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

# Broad keyword filter for deal-like text. It's fine if it catches things that aren't deals;
# the AI makes the final call. Add wording here whenever a real deal slips through.
DEAL_WORDS = re.compile(
    r"special|promo|deal|discount|coupon|happy hour|bogo|buy one|complimentary|\bfree\b|"
    r"half[- ]?(price|off)|kids eat|reward|loyalty|"
    r"\d+\s?% off|\$\d+\s?off|\d\s?for\s?\$\d+",
    re.I,
)

def looks_like_deal(text):
    return bool(text) and bool(DEAL_WORDS.search(text))