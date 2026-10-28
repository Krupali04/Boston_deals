"""One place for all AI calls: Gemini first, optional local Ollama backup."""
import json
import os
import tempfile
import time
from datetime import date
from typing import Literal, Optional

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"  # use the newest free Flash model in AI Studio
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:8b")         # backup: text + images, no video
FALLBACK = os.getenv("AI_FALLBACK", "none")                      # "ollama" on your laptop, "none" on GitHub
SECONDS_BETWEEN_CALLS = float(os.getenv("GEMINI_SECONDS_BETWEEN_CALLS", "4"))
INLINE_VIDEO_LIMIT = 18 * 1024 * 1024                            # larger videos go through Gemini's Files API
RETRIES = 3                                                      # tries per Gemini call on network problems
TEMPORARY_CODES = {500, 502, 503, 504}                           # Gemini server errors worth retrying

# ---------- The fixed answer formats ----------
OfferType = Literal["BOGO", "PERCENT_OFF", "DOLLAR_OFF", "FREE_ITEM", "HAPPY_HOUR", "FIRST_ORDER", "BUNDLE", "OTHER"]

class Deal(BaseModel):
    restaurant_name: Optional[str] = None
    offer_type: OfferType
    item: Optional[str] = None
    value: Optional[str] = None
    conditions: Optional[str] = None
    code: Optional[str] = None
    code_kind: Optional[Literal["general", "personal"]] = None
    valid_from: Optional[str] = None     # YYYY-MM-DD
    valid_until: Optional[str] = None    # YYYY-MM-DD
    confidence: float

class DealList(BaseModel):
    deals: list[Deal]

class HandlePick(BaseModel):
    handle: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    reason: str

class QuotaExceeded(Exception):
    """Gemini's free limit is used up and no backup model is available."""

class AITryLater(Exception):
    """Gemini couldn't answer for this item right now (network problem, overload, or a refused request).
    The caller should leave the item unread so it's retried on the next run."""

# ---------- Prompts ----------
DEAL_PROMPT = """Today is {today}. You extract restaurant and cafe deals from Instagram posts (caption plus any
attached images or video), website snippets, and news articles.

A deal is a discount or freebie. Offer types:
- BOGO: buy one, get one
- PERCENT_OFF or DOLLAR_OFF: a discount
- FREE_ITEM: something free with a purchase or for a group (for example, kids eat free)
- HAPPY_HOUR: time-limited food or drink prices
- FIRST_ORDER: a discount for first-time customers or new sign-ups
- BUNDLE: a set price for a combination (for example, 2 for $10)
- OTHER: any other real discount
NOT deals: regular menu items, normal prices, new-menu announcements, general perks (free Wi-Fi, free parking).

Read text shown inside images and videos, and listen to what is said in videos.
Write dates as YYYY-MM-DD, resolving phrases like "this Friday" using today's date; leave dates empty if
none are given. Set code_kind to "personal" for one-time or per-customer codes and "general" for codes
anyone can use. Set confidence between 0 and 1. Return an empty list if there is no deal.

Source text:
{text}"""

HANDLE_PROMPT = """Below are web search results. Choose the official Instagram account of this restaurant or cafe.
Restaurant: {name}
Address: {address}, Boston, MA {zip_code}

Search results:
{results}

Rules:
- Only choose a handle that appears in an instagram.com link in the results above.
- It must be the restaurant's own account (for this location, if it has several), not a food blogger,
  fan page, delivery app, or another business.
- confidence: "high" if the name and the Boston location clearly match, "medium" if the name matches but
  the location isn't confirmed, "low" if you are not sure.
- Leave handle empty if no result fits. Give a one-sentence reason."""

# ---------- Internals ----------
_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"]) if os.getenv("GEMINI_API_KEY") else None
_last_call = 0.0
_gemini_exhausted = False

def _wait():
    global _last_call
    delay = SECONDS_BETWEEN_CALLS - (time.time() - _last_call)
    if delay > 0:
        time.sleep(delay)
    _last_call = time.time()

def _upload_video(video):
    """Videos over the inline size limit are uploaded, processed by Gemini, then deleted after use."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video)
        path = f.name
    try:
        uploaded = _client.files.upload(file=path)
        while uploaded.state.name == "PROCESSING":
            time.sleep(3)
            uploaded = _client.files.get(name=uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini could not process the video ({uploaded.state.name})")
        return uploaded
    finally:
        os.remove(path)    # the video never stays on disk

def _gemini(prompt, schema, images, video):
    parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images]
    uploaded = None
    if video is not None:
        if len(video) <= INLINE_VIDEO_LIMIT:
            parts.append(types.Part.from_bytes(data=video, mime_type="video/mp4"))
        else:
            uploaded = _upload_video(video)
            parts.append(uploaded)
    _wait()
    try:
        resp = _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=parts + [prompt],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return resp.text
    finally:
        if uploaded is not None:
            _client.files.delete(name=uploaded.name)

def _gemini_with_retries(prompt, schema, images, video):
    """Call Gemini, retrying dropped connections, timeouts, and server overload a few times."""
    problem = "no answer"
    for attempt in range(1, RETRIES + 1):
        try:
            return _gemini(prompt, schema, images, video)
        except errors.APIError as e:
            if e.code not in TEMPORARY_CODES:
                raise                                   # 429 (limit) and other errors are handled in _ask
            problem = f"Gemini error {e.code}"
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            problem = type(e).__name__                  # e.g. the server disconnected without answering
        if attempt < RETRIES:
            print(f"  {problem} — retrying in {10 * attempt} seconds")
            time.sleep(10 * attempt)
    raise AITryLater(problem)

def _ollama(prompt, schema, images):
    import ollama    # only needed when the backup is used
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = list(images)
    resp = ollama.chat(model=OLLAMA_MODEL, messages=[msg],
                       format=schema.model_json_schema(), options={"temperature": 0})
    return resp["message"]["content"]

def _ask(prompt, schema, images=(), video=None):
    """Try Gemini; on a quota error switch to Ollama if allowed. Returns a checked answer, or None."""
    global _gemini_exhausted
    raw = None
    if _client and not _gemini_exhausted:
        try:
            raw = _gemini_with_retries(prompt, schema, images, video)
        except errors.APIError as e:
            if e.code != 429:
                raise AITryLater(f"Gemini error {e.code}: {e.message}")
            print("Gemini free limit reached for now.")
            _gemini_exhausted = True
    if raw is None:
        if FALLBACK != "ollama":
            raise QuotaExceeded()
        raw = _ollama(prompt, schema, images)    # Ollama can't watch video; callers also pass the cover image
    try:
        return schema.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        return None

# ---------- What scripts call ----------
def extract_deals(text, images=(), video=None):
    """Return a list of Deal objects found in the text, images, and/or video."""
    prompt = DEAL_PROMPT.format(today=date.today().isoformat(), text=(text or "none")[:6000])
    result = _ask(prompt, DealList, images, video)
    return result.deals if result else []

def pick_handle(name, address, zip_code, results):
    """Ask Gemini to choose the restaurant's own Instagram account from search results."""
    lines = [f"{n}. {r['title']} — {r['link']} — {r['snippet']}" for n, r in enumerate(results, 1)]
    prompt = HANDLE_PROMPT.format(name=name, address=address, zip_code=zip_code,
                                  results="\n".join(lines) or "no results")
    return _ask(prompt, HandlePick) or HandlePick(confidence="low", reason="AI reply could not be read")

def can_watch_video():
    """Only Gemini can watch videos, and only while its free limit lasts."""
    return _client is not None and not _gemini_exhausted