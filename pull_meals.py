"""
Lazy Man's Fast Food Diet -- daily meal puller.

What it does, once per run (the GitHub Action runs it daily):

  1. Asks Gemini 3.8 Flash for menu items from the approved restaurants
     below (RESTAURANTS) -- ONE restaurant per call. Each item comes back tagged as
     "breakfast", "regular" or "dessert" (desserts, slushes, shakes, malts --
     the planner only uses these as late night), plus a "healthy" yes/no and a
     "vegetarian" yes/no. Beverages and dips/sauces are excluded. Each call
     also gets the names already on file for that restaurant, so Gemini is
     asked for NEW items only; the JSON grows a little more every day.
  2. For every new meal, stores cost, calories and serving (as Gemini
     estimates them -- these are AI estimates, not live menu data).
     Serving rules applied by this script when saving (see apply_serving_rules):
     a single taco is stored as 3 tacos (calories and price x3) and a single
     pizza slice as 2 slices (calories x2; the price is doubled only for Pizza
     Hut slices priced under $7, other slice prices are left as reported).
     Items that are already multiple servings, personal/individual/kids'/whole
     pizzas, dessert pizzas, Gordita Crunches, and non-pizza "slice" items
     (cakes, pies, loaves) are left alone.
  3. Searches DuckDuckGo Images for every meal that has no picture yet and
     stores the image URL. The search puts the restaurant and the item in
     double quotes, e.g.  "Olive Garden" "Fettuccine Alfredo"  (find_image).
     refresh_images.py redoes every picture the same way. There is no cap on
     the number of searches; the phase ends when every meal has a picture,
     when DuckDuckGo starts rate-limiting (the rest wait for the next run), or
     after IMAGE_TIME_BUDGET_MINUTES so the job always finishes and commits
     meals.json.
  4. Writes everything to meals.json, which index.html and products.html read.

Resuming after errors: Gemini often answers 503 ("overloaded"). meals.json
keeps a "pending" list of restaurants not yet pulled in the current pass.
A restaurant only leaves that list after a successful pull, so the next run
starts exactly where this one left off. When the list empties, a fresh pass
over all the restaurants begins.

Models and daily limits: Gemini Flash 3.8, 3.7, 3.6 and 3.5 are tried in that
order. Each model gets its own daily allowance (MAX_GEMINI_CALLS per model, default
20), counted on the Pacific-time day Google's quota resets on. When a model is out of
quota (or rejects the request), the script moves to the next model; a 503
("overloaded") first tries the next model too. Only answers Google actually served
count toward a model's allowance. The Action runs 4 times a day; each run makes at most
20 Gemini calls (MAX_CALLS_PER_RUN; a full pass over the restaurants can take more than one run), so the day's
allowance is spread across the runs, failed restaurants get another try, and pictures
keep filling in.

Env var required: GEMINI_KEY  (missing -> the run is skipped, exit 0)
Optional env vars: MAX_GEMINI_CALLS (per model per day, default 20),
                   MAX_CALLS_PER_RUN (default 20)

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

# Tried in order. A model that answers 4xx (quota used up / unknown model) is skipped for
# the rest of the Pacific day; a model that keeps answering 5xx is skipped for this restaurant.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]

# The free-tier daily request limit you want to stay inside.
MAX_GEMINI_CALLS_PER_DAY = int(os.environ.get("MAX_GEMINI_CALLS", "20"))   # per model
MAX_GEMINI_CALLS_PER_RUN = int(os.environ.get("MAX_CALLS_PER_RUN", "20"))     # 4 runs a day use up 4 models x 20 calls
MAX_NEW_MEALS_PER_CALL = 60       # asked for per restaurant per call
KNOWN_NAMES_SENT_PER_CALL = 250   # already-known meal names sent so Gemini skips them
MIN_CALL_INTERVAL_SECONDS = 8     # stays well under per-minute limits
GEMINI_TIMEOUT_SECONDS = 120
GEMINI_MAX_RETRIES = 2            # network errors / 5xx only, per model per restaurant
GEMINI_RETRY_DELAY_SECONDS = 15
RETRY_PASS_DELAY_SECONDS = 60     # pause before the one extra pass over restaurants that failed

IMAGE_TIME_BUDGET_MINUTES = 60   # safety stop (not a count limit) so the Action finishes and commits
IMAGE_SEARCH_DELAY_SECONDS = 1.5
IMAGE_MAX_ATTEMPTS_PER_MEAL = 3   # then give up on that meal's picture
IMAGE_RATELIMIT_STRIKES = 4       # consecutive failures -> stop image phase this run
# Pictures are searched with the restaurant and the item in double quotes, e.g.
#   "Olive Garden" "Fettuccine Alfredo"
# IMAGE_STRATEGY names that method. Meals are marked with it ("_image_refreshed") once
# refresh_images.py has redone them; change it when the method changes and the next
# refresh redoes every picture again.
IMAGE_STRATEGY = "quoted-v1"
# If the quoted searches find nothing for a meal that has no picture at all, True falls
# back to an unquoted search ("<restaurant> <item>"); False leaves the meal without one.
IMAGE_FALLBACK_TO_WEB = True

# Sanity bounds so a hallucinated number can't poison the meal planner.
MIN_CALORIES, MAX_CALORIES = 20, 3500
MIN_PRICE, MAX_PRICE = 0.50, 80.00

# The ONLY restaurants meals may come from: national chains with locations in at
# least ~90% of the states. `except` = states with no locations (every other state
# has them). `site` = the restaurant's official website; its favicon is shown next
# to the delivery-app icons and links there. Shown on the web page too (index.html).
RESTAURANTS = [
    {"name": "Applebee's", "except": [], "site": "www.applebees.com"},
    {"name": "Arby's", "except": ["Rhode Island", "Vermont"], "site": "www.arbys.com"},
    {"name": "Buffalo Wild Wings", "except": [], "site": "www.buffalowildwings.com"},
    {"name": "Burger King", "except": [], "site": "www.bk.com"},
    {"name": "Chick-fil-A", "except": ["Vermont", "Alaska"], "site": "www.chick-fil-a.com"},
    {"name": "Chili's", "except": ["Oregon"], "site": "www.chilis.com"},
    {"name": "Chipotle", "except": ["Alaska", "Hawaii"], "site": "www.chipotle.com"},
    {"name": "Dairy Queen", "except": ["Vermont"], "site": "www.dairyqueen.com"},
    {"name": "Domino's", "except": [], "site": "www.dominos.com"},
    {"name": "Five Guys", "except": [], "site": "www.fiveguys.com"},
    {"name": "IHOP", "except": ["Vermont"], "site": "www.ihop.com"},
    {"name": "Jersey Mike's Subs", "except": [], "site": "www.jerseymikes.com"},
    {"name": "Kentucky Fried Chicken", "except": [], "site": "www.kfc.com"},
    {"name": "Little Caesar's", "except": [], "site": "www.littlecaesars.com"},
    {"name": "McDonald's", "except": [], "site": "www.mcdonalds.com"},
    {"name": "Olive Garden", "except": [], "site": "www.olivegarden.com"},
    {"name": "Outback Steakhouse", "except": ["Maine", "Vermont", "Rhode Island", "North Dakota"], "site": "www.outback.com"},
    {"name": "Panda Express", "except": ["Vermont"], "site": "www.pandaexpress.com"},
    {"name": "Panera", "except": ["Alaska", "Hawaii"], "site": "www.panerabread.com"},
    {"name": "Papa John's", "except": ["Vermont"], "site": "www.papajohns.com"},
    {"name": "Pizza Hut", "except": [], "site": "www.pizzahut.com"},
    {"name": "Popeyes", "except": ["Vermont", "Wyoming"], "site": "www.popeyes.com"},
    {"name": "Sonic Drive-In", "except": ["Maine", "New Hampshire", "Vermont"], "site": "www.sonicdrivein.com"},
    {"name": "Starbucks", "except": [], "site": "www.starbucks.com"},
    {"name": "Subway", "except": [], "site": "www.subway.com"},
    {"name": "Taco Bell", "except": [], "site": "www.tacobell.com"},
    {"name": "Texas Roadhouse", "except": ["Hawaii"], "site": "www.texasroadhouse.com"},
    {"name": "Wendy's", "except": [], "site": "www.wendys.com"},
]

RESTAURANT_NAMES = [r["name"] for r in RESTAURANTS]
RESTAURANT_SITES = {r["name"]: r["site"] for r in RESTAURANTS}   # official site: favicon + link on the web pages

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

SERVING RULES (follow exactly):
- A taco sold on its own: "serving" must be exactly "1 taco", and "calories" and "price" are for that ONE taco. Orders or packs of several tacos (e.g. "3 tacos", "order of 3 tacos", "2 rolled tacos") keep their real serving text and the calories and price of the whole order.
- A pizza sold by the slice: "serving" must start with "1 slice" (for example "1 slice (1/8 large pizza)"), and "calories" and "price" are for that ONE slice. Personal, individual, kids' or whole pizzas are NOT slices: give them as the whole item (for example "1 personal pizza").
- Do NOT multiply anything yourself. After your answer, the program turns a single taco into 3 tacos (calories and price x3) and a single pizza slice into 2 slices (calories x2; the price is only doubled for Pizza Hut slices). Just report the single taco or single slice.

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
_stats = {"ok": {}, "streak": {}}   # streak = consecutive 5xx/bad answers per model this run
FLAKY_AFTER = 3                    # skip a model for the rest of the run after this many in a row


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
    store.setdefault("quota", {"date": "", "calls": {}, "exhausted": []})
    store.setdefault("pending", [])   # restaurants still to pull in the current pass, in order
    # Keep the published restaurant list (and exclusions) in the JSON in sync
    # with this script so the web page always shows what the script uses.
    for r in RESTAURANTS:
        entry = store["restaurants"].setdefault(r["name"], {})
        entry["except_states"] = r["except"]
        entry["site"] = r["site"]
        entry.setdefault("last_pulled", "")
    store["allowed_restaurants"] = RESTAURANT_NAMES
    fixed = apply_serving_rules_to_store(store)
    if fixed:
        log(f"Serving rules: updated {fixed} saved meal(s) (single taco -> 3, single pizza slice -> 2).")
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

        _stats["ok"][model] = _stats["ok"].get(model, 0) + 1
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


def models_available(quota):
    """Models that still have allowance today and haven't been ruled out."""
    return [m for m in GEMINI_MODELS
            if m not in quota["exhausted"] and quota["calls"].get(m, 0) < MAX_GEMINI_CALLS_PER_DAY]


def ask_gemini(prompt, key, limiter, quota):
    """Returns (items, model_used). Walks the model list in order, skipping models that
    are used up for today. 4xx -> model is marked used up for the day; 5xx / bad
    output -> try the next model for this restaurant only."""
    transient = None
    for model in models_available(quota):
        if _stats["streak"].get(model, 0) >= FLAKY_AFTER:
            continue                       # overloaded all run long; the next run tries it again
        before = _stats["ok"].get(model, 0)
        try:
            items = call_model(prompt, key, model, limiter)
            _stats["streak"][model] = 0
            return items, model
        except _ModelUnavailable as e:
            quota["exhausted"].append(model)
            log(f"  {model} unavailable for today ({e}) -- trying the next model.")
        except Exception as e:  # noqa: BLE001
            transient = e
            _stats["streak"][model] = _stats["streak"].get(model, 0) + 1
            log(f"  {model} failed ({e}) -- trying the next model.")
        finally:
            # Only answers Google actually served count toward the model's daily allowance.
            quota["calls"][model] = quota["calls"].get(model, 0) + _stats["ok"].get(model, 0) - before
    if transient:
        raise transient
    raise QuotaExceeded("every model is used up for today")


def _and_list(items):
    """['A'] -> 'A', ['A','B'] -> 'A and B', ['A','B','C'] -> 'A, B and C'"""
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def build_prompt(restaurant, known_names):
    r = next(x for x in RESTAURANTS if x["name"] == restaurant)
    if r["except"]:
        availability = ("It has locations in every U.S. state except "
                        + _and_list(r["except"]) + ".")
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


# --- Serving rules applied when a meal is saved --------------------------------
# A lone taco is stored as 3 tacos (calories and price x3); a lone pizza slice is
# stored as 2 slices (calories x2; price doubled only for Pizza Hut slices that are
# priced like a single slice, i.e. under SLICE_PRICE_DOUBLE_BELOW -- other chains'
# "slice" prices are really whole-pizza prices, so they are left as reported). Anything that is already
# several servings, a personal / individual / kids' / whole pizza, a dessert pizza,
# a Gordita Crunch, or a "slice" of something that isn't pizza (cheesecake, pie,
# loaf, ham...) is left alone. The rule only fires on a
# serving that reads like ONE taco / ONE slice, and rewriting the serving text
# makes it fire only once, so running it again on saved meals changes nothing.
TACO_COUNT, PIZZA_SLICE_COUNT = 3, 2
SLICE_PRICE_DOUBLED_FOR = {"Pizza Hut"}
SLICE_PRICE_DOUBLE_BELOW = 7.00
_ONE = r"(?:1|one|a|single)"
_TACO_SERVING_RE = re.compile(rf"^\s*{_ONE}\s+(?:[a-z'’\-]+\s+){{0,2}}tacos?\s*(?:\(.*\))?\s*$", re.I)
_SLICE_SERVING_RE = re.compile(rf"^\s*{_ONE}\s+slice\b", re.I)
_PIZZA_WORD_RE = re.compile(r"\bpizza\b", re.I)
_NOT_A_SLICE_RE = re.compile(r"\+|&|\bwith\b|\band\b|stix|personal|individual|kid|mini|whole|calzone", re.I)
_NEVER_SCALE_NAME_RE = re.compile(r"gordita|treatzza", re.I)   # not "tacos" / dessert pizzas: leave as reported


def apply_serving_rules(meal):
    """Turns a single taco into 3 (calories and price x3) and a single pizza slice into 2
    (calories x2; price x2 only for Pizza Hut slices under $7). Returns True if the meal was changed."""
    if meal.get("_serving_multiplier"):
        return False                                   # already done
    name, serving = meal.get("name", ""), str(meal.get("serving", ""))
    if meal.get("category") == "dessert" or _NEVER_SCALE_NAME_RE.search(name):
        return False                                   # dessert pizzas and Gordita Crunches stay as they are
    price_mult = 1
    if _TACO_SERVING_RE.match(serving):
        mult = price_mult = TACO_COUNT
        serving = re.sub(rf"^\s*{_ONE}\s+", f"{mult} ", serving, flags=re.I)
        serving = re.sub(r"\btaco\b", "tacos", serving, count=1, flags=re.I)
    elif (_SLICE_SERVING_RE.match(serving)
          and (_PIZZA_WORD_RE.search(name) or _PIZZA_WORD_RE.search(serving))
          and not _NOT_A_SLICE_RE.search(serving) and not _NOT_A_SLICE_RE.search(name)):
        mult = PIZZA_SLICE_COUNT
        if meal.get("restaurant") in SLICE_PRICE_DOUBLED_FOR and float(meal["price"]) < SLICE_PRICE_DOUBLE_BELOW:
            price_mult = mult
        serving = re.sub(rf"^\s*{_ONE}\s+slice\b", f"{mult} slices", serving, flags=re.I)
        serving = re.sub(r"\b1/(\d+)\b", rf"{mult}/\1", serving)   # "1/8 pizza" -> "2/8 pizza"
    else:
        return False
    meal["calories"] = int(round(float(meal["calories"]) * mult))
    meal["price"] = round(float(meal["price"]) * price_mult, 2)
    meal["serving"] = serving
    meal["_serving_multiplier"] = mult
    # "Single Chicken Taco" no longer makes sense once it is 3 tacos.
    meal["name"] = re.sub(r"^single\s+", "", name, flags=re.I).strip() or name
    return True


def apply_serving_rules_to_store(store):
    """One-time (and harmless to repeat) pass over meals already saved in meals.json."""
    return sum(1 for m in store["meals"] if apply_serving_rules(m))


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
    meal = {
        "restaurant": restaurant,
        "name": name,
        "category": category,
        "calories": calories,
        "price": price,
        "serving": serving,
        "vegetarian": bool(raw.get("vegetarian")),
        "healthy": bool(raw.get("healthy")) and category != "dessert",
    }
    apply_serving_rules(meal)   # single taco -> 3 tacos, single pizza slice -> 2 slices
    return meal


#---------------------------------------------------------------------------
# DuckDuckGo image search
#---------------------------------------------------------------------------

_ddgs_client = None


def _client(reset=False):
    """One shared DuckDuckGo session (new connections for every search look like a bot);
    after an error a fresh one is made for the next search."""
    global _ddgs_client
    if reset or _ddgs_client is None:
        _ddgs_client = DDGS()
    return _ddgs_client


def _first_https_image(query):
    """One DuckDuckGo image search. Returns (thumbnail_url, full_url) or (None, None)."""
    try:
        results = _client().images(query, max_results=6, safesearch="moderate")
    except Exception:
        _client(reset=True)
        raise
    for res in results or []:
        thumb = (res.get("thumbnail") or "").strip()
        full = (res.get("image") or "").strip()
        # https only: an http image would be blocked on an https site.
        if thumb.startswith("https://") or full.startswith("https://"):
            return (thumb if thumb.startswith("https://") else full,
                    full if full.startswith("https://") else "")
    return None, None


def _quote(text):
    """Wraps text in double quotes for the search box (any quotes inside are dropped)."""
    return '"' + str(text).replace('"', "").strip() + '"'


def image_queries(restaurant, name, fallback):
    """The searches to try, in order, as (query, source) pairs. The first is what you would
    type by hand:  "Olive Garden" "Fettuccine Alfredo"  -- then the same without any
    "(...)" in the item name, then (if allowed) the plain unquoted words."""
    queries = [(f"{_quote(restaurant)} {_quote(name)}", "quoted")]
    short = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if short and short.lower() != name.strip().lower():
        queries.append((f"{_quote(restaurant)} {_quote(short)}", "quoted"))
    if fallback:
        queries.append((f"{restaurant} {name}", "web"))
    return queries


def find_image(restaurant, name, fallback=None):
    """Searches DuckDuckGo Images with the restaurant and item in double quotes.
    Returns (thumbnail, full, source) -- source is "quoted" or "web" (unquoted fallback) --
    or (None, None, None) when nothing was found."""
    if fallback is None:
        fallback = IMAGE_FALLBACK_TO_WEB
    for i, (query, source) in enumerate(image_queries(restaurant, name, fallback)):
        if i:
            time.sleep(IMAGE_SEARCH_DELAY_SECONDS)       # pause between searches for the same meal
        thumb, full = _first_https_image(query)
        if thumb:
            return thumb, full, source
    return None, None, None


def fill_images(store):
    if DDGS is None:
        log("ddgs isn't installed (pip install ddgs) -- skipping image search.")
        return 0
    todo = [m for m in store["meals"]
            if not m.get("image_url")
            and m.get("_image_attempts", 0) < IMAGE_MAX_ATTEMPTS_PER_MEAL]
    if not todo:
        log("Images: every meal already has a picture (or has used its attempts).")
        return 0
    log(f"Images: {len(todo)} meal(s) need a picture; searching until done or rate-limited.")

    found, strikes = 0, 0
    deadline = time.monotonic() + IMAGE_TIME_BUDGET_MINUTES * 60
    for i, meal in enumerate(todo, start=1):
        if time.monotonic() > deadline:
            log(f"  image time budget ({IMAGE_TIME_BUDGET_MINUTES} min) reached -- the rest wait for the next run.")
            break
        try:
            thumb, full, source = find_image(meal["restaurant"], meal["name"])
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
        meal.pop("_image_site_checked", None)    # leftover marker from the old site: search
        if thumb:
            meal["image_url"] = thumb
            meal["image_full_url"] = full or ""
            meal["_image_source"] = source       # "quoted" = quoted search, "web" = unquoted fallback
            meal["_image_refreshed"] = IMAGE_STRATEGY
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
        fill_images(store)

    save_store(args.output, store)
    by_cat = {c: sum(1 for m in store["meals"] if m["category"] == c) for c in CATEGORIES}
    log(f"Done. meals.json now has {len(store['meals'])} meals "
        f"({by_cat['breakfast']} breakfast, {by_cat['regular']} regular).")


def pull_one(store, restaurant, key, limiter, known):
    """One Gemini call for one restaurant. Returns the number of meals added,
    or None if the call failed (the restaurant then stays in the pending list)."""
    known_names = [m["name"] for m in store["meals"] if m["restaurant"] == restaurant]
    prompt = build_prompt(restaurant, known_names)
    raw_items, model_used = ask_gemini(prompt, key, limiter, store["quota"])

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
        f"({len(raw_items)} returned by {model_used}).")
    return added


def pull_meals(store, key, out_path):
    today = pacific_today()
    quota = store["quota"]
    if quota.get("date") != today:
        quota.clear()
        quota.update({"date": today, "calls": {}, "exhausted": []})
    quota.setdefault("calls", {})
    quota.setdefault("exhausted", [])
    budget = sum(MAX_GEMINI_CALLS_PER_DAY - quota["calls"].get(m, 0) for m in models_available(quota))
    if budget <= 0:
        log(f"Every model is at its daily limit ({MAX_GEMINI_CALLS_PER_DAY} calls each on {today} Pacific) "
            f"-- no meals pulled this run.")
        return

    # Resume where the last run left off: "pending" holds the restaurants not yet
    # pulled in the current pass. Empty (or first run) -> start a fresh pass.
    pending = [n for n in store.get("pending", []) if n in RESTAURANT_NAMES]
    if not pending:
        pending = list(RESTAURANT_NAMES)
    # Restaurants that have never been pulled (e.g. just added to RESTAURANTS) go first.
    never = [n for n in RESTAURANT_NAMES
             if n not in pending and not store["restaurants"].get(n, {}).get("last_pulled")]
    pending = never + pending
    store["pending"] = pending
    log(f"Gemini: up to {budget} call(s) left today across {len(models_available(quota))} model(s). Resuming at {pending[0]} "
        f"({len(pending)} restaurant(s) left in this pass).")

    known = {meal_key(m["restaurant"], m["name"]) for m in store["meals"]}
    limiter = _RateLimiter(MIN_CALL_INTERVAL_SECONDS)
    queue = list(pending)
    failed, retried = [], False
    successes = attempts = added_total = 0
    max_attempts = budget * 2          # failures can't make this loop forever

    while models_available(quota) and successes < MAX_GEMINI_CALLS_PER_RUN and attempts < max_attempts:
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
            log(f"Finished a full pass over all {len(RESTAURANT_NAMES)} restaurants -- starting another.")
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
        f"calls used today (Pacific) per model: {quota['calls']}. "
        f"Next run starts at: {nxt}.")
    save_store(out_path, store)


if __name__ == "__main__":
    main()
