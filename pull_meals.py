"""
Lazy Man's Fast Food Diet Plan -- daily meal puller.

What it does, once per run (the GitHub Action runs it daily):

  1. Asks Gemini 3.8 Flash for menu items from the 20 approved restaurants
     below -- ONE restaurant per call. Each item comes back tagged as
     "breakfast", "regular" or "dessert" (desserts, slushes, shakes, malts --
     the planner only uses these as late night), plus a "healthy" yes/no and a
     "vegetarian" yes/no. Beverages and dips/sauces are excluded. Each call
     also gets the names already on file for that restaurant, so Gemini is
     asked for NEW items only; the JSON grows a little more every day.
  2. For every new meal, stores cost, calories and serving (as Gemini
     estimates them -- these are AI estimates, not live menu data).
  3. Searches DuckDuckGo Images (at most 250 searches per run, because DDG
     rate-limits) for meals that have no picture yet and stores the image URL.
  4. Writes everything to meals.json, which index.html and products.html read.

Resuming after errors: Gemini often answers 503 ("overloaded"). meals.json
keeps a "pending" list of restaurants not yet pulled in the current pass.
A restaurant only leaves that list after a successful pull, so the next run
starts exactly where this one left off. When the list empties, a fresh pass
over all 20 begins.

Daily limit: Gemini 3.8 Flash is treated as 20 successful requests/day
(MAX_GEMINI_CALLS_PER_DAY), counted against the Pacific-time day (the day
Google's quota resets on). Failed 503s are not counted.

Env var required: GEMINI_KEY  (missing -> the run is skipped, exit 0)
Optional env var: MAX_GEMINI_CALLS (default 20)

Run locally:  GEMINI_KEY=xxxx python pull_meals.py
Dependencies: pip install requests ddgs
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

try:  # the package was renamed from duckduckgo_search to ddgs
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(_SCRIPT_DIR, "meals.json")

#---------------------------------------------------------------------------
# Config
#---------------------------------------------------------------------------

# Tried in order; a 4xx (rate limit / quota / unknown model) moves to the next.
# Only 3.8 Flash on purpose -- add fallbacks here if you ever want them.
GEMINI_MODELS = ["gemini-3.5-flash"]

# The free-tier daily request limit you want to stay inside.
MAX_GEMINI_CALLS_PER_DAY = int(os.environ.get("MAX_GEMINI_CALLS", "20"))
MAX_NEW_MEALS_PER_CALL = 60       # asked for per restaurant per call
KNOWN_NAMES_SENT_PER_CALL = 250   # already-known meal names sent so Gemini skips them
MIN_CALL_INTERVAL_SECONDS = 8     # stays well under per-minute limits
GEMINI_TIMEOUT_SECONDS = 120
GEMINI_MAX_RETRIES = 3            # network errors / 5xx only, per restaurant attempt
GEMINI_RETRY_DELAY_SECONDS = 15
RETRY_PASS_DELAY_SECONDS = 60     # pause before the one extra pass over restaurants that failed

MAX_IMAGE_SEARCHES_PER_RUN = 250  # hard cap per run -- DuckDuckGo rate-limits
IMAGE_SEARCH_DELAY_SECONDS = 1.5
IMAGE_MAX_ATTEMPTS_PER_MEAL = 3   # then give up on that meal's picture
IMAGE_RATELIMIT_STRIKES = 4       # consecutive failures -> stop image phase this run

# Sanity bounds so a hallucinated number can't poison the meal planner.
MIN_CALORIES, MAX_CALORIES = 20, 3500
MIN_PRICE, MAX_PRICE = 0.50, 80.00

# The ONLY restaurants meals may come from. `except` = states with no locations
# (every other state has them). Shown on the web page too (index.html).
RESTAURANTS = [
    {"name": "Applebee's",              "except": []},
    {"name": "Burger King",             "except": []},
    {"name": "Chick-fil-A",             "except": ["Vermont", "Alaska"]},
    {"name": "Chipotle",                "except": ["Alaska", "Hawaii"]},
    {"name": "Dairy Queen",             "except": ["Vermont"]},
    {"name": "Domino's",                "except": []},
    {"name": "Five Guys",               "except": []},
    {"name": "IHOP",                    "except": ["Vermont"]},
    {"name": "Jersey Mike's Subs",      "except": []},
    {"name": "Kentucky Fried Chicken",  "except": []},
    {"name": "Little Caesar's",         "except": []},
    {"name": "McDonald's",              "except": []},
    {"name": "Olive Garden",            "except": []},
    {"name": "Panera",                  "except": ["Alaska", "Hawaii"]},
    {"name": "Pizza Hut",               "except": []},
    {"name": "Starbucks",               "except": []},
    {"name": "Subway",                  "except": []},
    {"name": "Taco Bell",               "except": []},
    {"name": "Texas Roadhouse",         "except": ["Hawaii"]},
    {"name": "Wendy's",                 "except": []},
]
RESTAURANT_NAMES = [r["name"] for r in RESTAURANTS]

CATEGORIES = ("breakfast", "regular", "dessert")

PROMPT = """You are building a menu database for a calorie-counting meal planner.

Restaurant: {restaurant}
This restaurant is a U.S. national chain. {availability}

List as many DIFFERENT menu items as you can that are (or were recently) sold on the standard U.S. menu of {restaurant} -- and ONLY {restaurant}. Never include items from any other restaurant, and never invent items. If you are not confident an item exists, leave it out.

Sort every item into one of three categories:
- "breakfast": items sold as part of the breakfast menu (breakfast sandwiches, burritos, bowls, platters, pancakes, oatmeal, breakfast pastries, etc.). Many restaurants have none -- then return no breakfast items.
- "dessert": desserts, ice cream, sundaes, Blizzards, cookies, pies, brownies, cinnamon rolls, slushes/frozen slushies, milkshakes, malts, and sweet blended frozen drinks (such as Frappuccinos).
- "regular": everything else you would eat as a meal or a real portion: entrees, sandwiches, burgers, wraps, bowls, pizzas (one slice or the standard single serving, not the whole party-size pizza), salads, chicken meals, and sides worth counting (fries, breadsticks, soup, etc.).

DO NOT include:
- Beverages of any kind (soda, water, coffee, tea, juice, lemonade, hot chocolate, smoothies and other drinks) -- the only drinks allowed are slushes, shakes, malts and sweet blended frozen drinks, which count as "dessert".
- Dips, sauces, dressings, condiments, toppings and add-ons.

For each item give:
- "name": the menu name, e.g. "Big Mac" (do not include the restaurant name)
- "category": "breakfast", "regular" or "dessert"
- "calories": calories for ONE serving exactly as that item is normally ordered (whole sandwich, one slice, one standard side, one regular-size shake) -- an integer
- "price": typical U.S. menu price in dollars for that single item (not a combo meal unless the item IS a combo) -- a number such as 6.49. This is an estimate; prices vary by location.
- "serving": a short description of what one serving is, e.g. "1 sandwich", "2 slices", "medium fries", "1 bowl (14 oz)"
- "vegetarian": true only if the item as normally served has no meat, poultry or fish
- "healthy": true only if a reasonable dietitian would call it one of the healthier choices on this menu -- for example grilled or lean protein, salads and veggie-forward bowls, oatmeal, fruit, or a lower-calorie item that is not deep-fried and not heavy on sugar or cheese. Desserts, shakes, fried items and heavy combo-style items are normally false.

Return at most {max_items} items, best-known and most-ordered first. Do NOT include any item whose name appears in the ALREADY KNOWN list below (treat small spelling differences as the same item). If you cannot find any more genuinely new items, return fewer -- or an empty array.

ALREADY KNOWN for {restaurant} ({known_count} items):
{known_block}

Return ONLY a JSON array of objects with exactly the keys above."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "category": {"type": "STRING", "enum": list(CATEGORIES)},
            "calories": {"type": "INTEGER"},
            "price": {"type": "NUMBER"},
            "serving": {"type": "STRING"},
            "vegetarian": {"type": "BOOLEAN"},
            "healthy": {"type": "BOOLEAN"},
        },
        "required": ["name", "category", "calories", "price", "serving", "vegetarian", "healthy"],
    },
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# Counts HTTP 200 answers from Gemini (those use up daily quota; 503s do not).
_stats = {"ok_responses": 0}


class QuotaExceeded(RuntimeError):
    """Every model in the chain said no (rate limit / quota / unavailable)."""


class _ModelUnavailable(Exception):
    """A single model returned a 4xx -- move on to the next model."""


#---------------------------------------------------------------------------
# JSON store
#---------------------------------------------------------------------------

def pacific_today():
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def load_store(path):
    store = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                store = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"Could not read {path}: {e} -- fix or delete it and re-run.")
    if not isinstance(store, dict):
        raise SystemExit(f"{path} should hold a JSON object; refusing to overwrite it.")

    store.setdefault("meals", [])
    store.setdefault("restaurants", {})
    store.setdefault("quota", {"date": "", "gemini_calls": 0})
    store.setdefault("pending", [])   # restaurants still to pull in the current pass, in order
    # Keep the published restaurant list (and exclusions) in the JSON in sync
    # with this script so the web page always shows what the script uses.
    for r in RESTAURANTS:
        entry = store["restaurants"].setdefault(r["name"], {})
        entry["except_states"] = r["except"]
        entry.setdefault("last_pulled", "")
    store["allowed_restaurants"] = RESTAURANT_NAMES
    return store


def save_store(path, store):
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def normalize_name(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).strip()


def meal_key(restaurant, name):
    return f"{restaurant.lower()}|{normalize_name(name)}"


#---------------------------------------------------------------------------
# Gemini
#---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self, min_interval):
        self._min_interval = min_interval
        self._next_allowed = 0.0

    def wait(self):
        now = time.monotonic()
        start_at = max(now, self._next_allowed)
        self._next_allowed = start_at + self._min_interval
        if start_at > now:
            time.sleep(start_at - now)


def extract_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON array in response: {text[:200]!r}")
        parsed = json.loads(match.group(0))
    if isinstance(parsed, dict):  # some responses wrap the array in an object
        for value in parsed.values():
            if isinstance(value, list):
                return value
    if not isinstance(parsed, list):
        raise ValueError("parsed JSON was not an array")
    return parsed


def call_model(prompt, key, model, limiter):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last_err = None
    for attempt in range(GEMINI_MAX_RETRIES):
        limiter.wait()
        try:
            resp = requests.post(
                url,
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                        "responseSchema": RESPONSE_SCHEMA,
                        "maxOutputTokens": 32768,
                    },
                },
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            last_err = e
            log(f"  {model}: request error (attempt {attempt + 1}/{GEMINI_MAX_RETRIES}): {e}")
            time.sleep(GEMINI_RETRY_DELAY_SECONDS)
            continue

        if 400 <= resp.status_code < 500:
            raise _ModelUnavailable(f"{resp.status_code} {resp.reason}: {resp.text[:300]}")
        if resp.status_code >= 500:
            last_err = RuntimeError(f"{resp.status_code} {resp.reason}")
            log(f"  {model}: {resp.status_code} (attempt {attempt + 1}/{GEMINI_MAX_RETRIES})")
            time.sleep(GEMINI_RETRY_DELAY_SECONDS)
            continue

        _stats["ok_responses"] += 1
        try:
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            return extract_json_array(text)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  {model}: unusable response (attempt {attempt + 1}/{GEMINI_MAX_RETRIES}): {e}")
            time.sleep(GEMINI_RETRY_DELAY_SECONDS)

    raise last_err or RuntimeError(f"{model} failed for an unknown reason")


def ask_gemini(prompt, key, limiter):
    """Returns (items, model_used). The caller counts each restaurant call
    against the daily budget once, before making it."""
    last = None
    for model in GEMINI_MODELS:
        try:
            return call_model(prompt, key, model, limiter), model
        except _ModelUnavailable as e:
            last = e
            log(f"  {model} unavailable ({e}) -- trying next model.")
    raise QuotaExceeded(f"all models unavailable (last: {last})")


def build_prompt(restaurant, known_names):
    r = next(x for x in RESTAURANTS if x["name"] == restaurant)
    if r["except"]:
        availability = ("It has locations in every U.S. state except "
                        + " and ".join(r["except"]) + ".")
    else:
        availability = "It has locations in all 50 U.S. states."
    shown = known_names[-KNOWN_NAMES_SENT_PER_CALL:]
    known_block = "\n".join(f"- {n}" for n in shown) if shown else "(none yet)"
    return PROMPT.format(
        restaurant=restaurant,
        availability=availability,
        max_items=MAX_NEW_MEALS_PER_CALL,
        known_count=len(known_names),
        known_block=known_block,
    )


def clean_meal(raw, restaurant):
    """Validates one Gemini item. Returns a meal dict or None."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name or len(name) > 120:
        return None
    # Drop a leading "<Restaurant> " the model sometimes adds.
    name = re.sub(rf"^{re.escape(restaurant)}[\s'’]*s?\s+", "", name, flags=re.I).strip() or name

    category = str(raw.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        return None
    try:
        calories = int(round(float(raw.get("calories"))))
        price = round(float(str(raw.get("price")).replace("$", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    if not (MIN_CALORIES <= calories <= MAX_CALORIES and MIN_PRICE <= price <= MAX_PRICE):
        return None
    serving = str(raw.get("serving", "")).strip()[:80] or "1 serving"
    return {
        "restaurant": restaurant,
        "name": name,
        "category": category,
        "calories": calories,
        "price": price,
        "serving": serving,
        "vegetarian": bool(raw.get("vegetarian")),
        "healthy": bool(raw.get("healthy")) and category != "dessert",
    }


#---------------------------------------------------------------------------
# DuckDuckGo image search
#---------------------------------------------------------------------------

def find_image(restaurant, name):
    """Returns (thumbnail_url, full_url) or (None, None) when nothing usable."""
    query = f"{restaurant} {name}"
    results = DDGS().images(query, max_results=6, safesearch="moderate")
    for res in results or []:
        thumb = (res.get("thumbnail") or "").strip()
        full = (res.get("image") or "").strip()
        # https only: an http image would be blocked on an https site.
        if thumb.startswith("https://") or full.startswith("https://"):
            return (thumb if thumb.startswith("https://") else full,
                    full if full.startswith("https://") else "")
    return None, None


def fill_images(store, limit):
    if DDGS is None:
        log("ddgs isn't installed (pip install ddgs) -- skipping image search.")
        return 0
    todo = [m for m in store["meals"]
            if not m.get("image_url")
            and m.get("_image_attempts", 0) < IMAGE_MAX_ATTEMPTS_PER_MEAL]
    if not todo:
        log("Images: every meal already has a picture (or has used its attempts).")
        return 0
    log(f"Images: {len(todo)} meal(s) need a picture; searching up to {limit} this run.")

    found, strikes = 0, 0
    for i, meal in enumerate(todo[:limit], start=1):
        try:
            thumb, full = find_image(meal["restaurant"], meal["name"])
            strikes = 0
        except Exception as e:  # noqa: BLE001 -- DDG throws many error types when rate limiting
            strikes += 1
            log(f"  image search failed for {meal['restaurant']} {meal['name']}: {e}")
            if strikes >= IMAGE_RATELIMIT_STRIKES:
                log("  too many image-search failures in a row (likely rate-limited) -- "
                    "stopping the image phase; the rest wait for the next run.")
                break
            time.sleep(IMAGE_SEARCH_DELAY_SECONDS * 5 * strikes)
            continue

        meal["_image_attempts"] = meal.get("_image_attempts", 0) + 1
        if thumb:
            meal["image_url"] = thumb
            meal["image_full_url"] = full or ""
            found += 1
        if i % 25 == 0:
            log(f"  ...{i} searched, {found} pictures found so far")
        time.sleep(IMAGE_SEARCH_DELAY_SECONDS)
    log(f"Images: {found} picture(s) added.")
    return found


#---------------------------------------------------------------------------
# Main
#---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default=OUTPUT_PATH, help="Path to meals.json")
    parser.add_argument("--skip-images", action="store_true", help="Only pull meals, skip DuckDuckGo")
    parser.add_argument("--images-only", action="store_true", help="Only fill missing pictures, skip Gemini")
    args = parser.parse_args()

    store = load_store(args.output)

    if not args.images_only:
        key = os.environ.get("GEMINI_KEY")
        if not key:
            log("No GEMINI_KEY set -- skipping the Gemini meal pull.")
        else:
            pull_meals(store, key, args.output)

    if not args.skip_images:
        fill_images(store, MAX_IMAGE_SEARCHES_PER_RUN)

    save_store(args.output, store)
    by_cat = {c: sum(1 for m in store["meals"] if m["category"] == c) for c in CATEGORIES}
    log(f"Done. meals.json now has {len(store['meals'])} meals "
        f"({by_cat['breakfast']} breakfast, {by_cat['regular']} regular).")


def pull_one(store, restaurant, key, limiter, known):
    """One Gemini call for one restaurant. Returns the number of meals added,
    or None if the call failed (the restaurant then stays in the pending list)."""
    known_names = [m["name"] for m in store["meals"] if m["restaurant"] == restaurant]
    prompt = build_prompt(restaurant, known_names)
    before = _stats["ok_responses"]
    try:
        raw_items, model_used = ask_gemini(prompt, key, limiter)
    finally:
        # Only answers Google actually served count toward the daily limit.
        store["quota"]["gemini_calls"] += _stats["ok_responses"] - before

    now = datetime.now(timezone.utc).isoformat()
    added = rejected = dupes = 0
    for raw in raw_items:
        meal = clean_meal(raw, restaurant)
        if meal is None:
            rejected += 1
            continue
        k = meal_key(restaurant, meal["name"])
        if k in known:
            dupes += 1
            continue
        known.add(k)
        meal["_added_at"] = now
        meal["_model"] = model_used
        store["meals"].append(meal)
        added += 1
    store["restaurants"][restaurant]["last_pulled"] = now
    log(f"  {restaurant}: +{added} new, {dupes} already known, {rejected} rejected "
        f"({len(raw_items)} returned).")
    return added


def pull_meals(store, key, out_path):
    today = pacific_today()
    quota = store["quota"]
    if quota.get("date") != today:
        quota["date"], quota["gemini_calls"] = today, 0
    budget = MAX_GEMINI_CALLS_PER_DAY - quota["gemini_calls"]
    if budget <= 0:
        log(f"Daily limit reached ({MAX_GEMINI_CALLS_PER_DAY} Gemini calls on {today} Pacific) "
            f"-- no meals pulled this run.")
        return

    # Resume where the last run left off: "pending" holds the restaurants not yet
    # pulled in the current pass. Empty (or first run) -> start a fresh pass.
    pending = [n for n in store.get("pending", []) if n in RESTAURANT_NAMES]
    if not pending:
        pending = list(RESTAURANT_NAMES)
    store["pending"] = pending
    log(f"Gemini: {budget} call(s) left today. Resuming at {pending[0]} "
        f"({len(pending)} restaurant(s) left in this pass).")

    known = {meal_key(m["restaurant"], m["name"]) for m in store["meals"]}
    limiter = _RateLimiter(MIN_CALL_INTERVAL_SECONDS)
    queue = list(pending)
    failed, retried = [], False
    successes = attempts = added_total = 0
    max_attempts = budget * 2          # failures can't make this loop forever

    while quota["gemini_calls"] < MAX_GEMINI_CALLS_PER_DAY and attempts < max_attempts:
        if not queue:
            if failed and not retried:
                retried = True
                log(f"  {len(failed)} restaurant(s) failed -- waiting {RETRY_PASS_DELAY_SECONDS}s "
                    f"for one more try: {', '.join(failed)}")
                time.sleep(RETRY_PASS_DELAY_SECONDS)
                queue, failed = failed, []
                continue
            if failed:
                break                  # still failing; they stay at the front of "pending"
            # Whole pass finished with quota to spare: start the next pass.
            store["pending"] = pending = list(RESTAURANT_NAMES)
            queue, retried = list(pending), False
            log("Finished a full pass over all 20 restaurants -- starting another.")
            continue

        restaurant = queue.pop(0)
        attempts += 1
        try:
            added = pull_one(store, restaurant, key, limiter, known)
        except QuotaExceeded as e:
            log(f"  {restaurant}: {e} -- stopping; the next run continues here.")
            save_store(out_path, store)
            return
        except Exception as e:  # noqa: BLE001 -- one bad call shouldn't kill the run
            log(f"  {restaurant}: failed ({e}); it stays pending for the next try.")
            failed.append(restaurant)
            save_store(out_path, store)
            continue

        successes += 1
        added_total += added
        if restaurant in pending:
            pending.remove(restaurant)
        store["pending"] = pending
        save_store(out_path, store)    # progress survives an interrupted run

    nxt = store["pending"][0] if store["pending"] else "(a fresh pass)"
    log(f"Gemini: {successes} restaurant(s) pulled, {added_total} new meal(s); "
        f"{quota['gemini_calls']}/{MAX_GEMINI_CALLS_PER_DAY} calls used for {today} (Pacific). "
        f"Next run starts at: {nxt}.")
    save_store(out_path, store)


if __name__ == "__main__":
    main()
