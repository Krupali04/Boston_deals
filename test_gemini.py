import os
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
print("Model:", model)

tests = {
    "normal": types.GenerateContentConfig(temperature=0),
    "search": types.GenerateContentConfig(temperature=0, tools=[types.Tool(google_search=types.GoogleSearch())]),
}
for name, config in tests.items():
    try:
        r = client.models.generate_content(model=model, contents="What is the official website of the Museum of Fine Arts, Boston?", config=config)
        print(f"{name}: OK — {r.text[:80]!r}")
    except errors.APIError as e:
        print(f"{name}: ERROR {e.code} — {e.message}")