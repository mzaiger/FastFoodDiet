"""
Lazy Man's Fast Food Diet -- meal puller (run by hand: GitHub Actions > "Pull fast food meals (manual)").

STEP 1 of a two-step manual process. This script only pulls MEALS. It does not look
for pictures. Step 2 is refresh_images.py ("Fill missing meal images (manual)"), which
finds pictures for every meal whose image is still empty.

What it does, once per run:

  1. Asks Gemini for menu items from the approved restaurants below (RESTAURANTS) --
     ONE restaurant per call. Each item comes back tagged as "breakfast", "regular" or
     "dessert" (desserts, slushes, shakes, malts -- the planner only uses these as late
     night), plus a "healthy" yes/no and a "vegetarian" yes/no. Beverages and
     dips/sauces are excluded. Each call also gets the names already on file for that
     restaurant, so Gemini is asked for NEW items only; the JSON grows with every pull.
  2. For every new meal, stores cost, calories and serving (as Gemini estimates them --
     these are AI estimates, not live menu data). The picture is left EMPTY
     (image_url and image_full_url are ""); refresh_images.py fills it in later.
     Serving rules applied by this script when saving (see apply_serving_rules):
     a single taco is stored as 3 tacos (calories and price x3) and a single
     pizza slice as 2 slices (calories x2; the price is doubled only for Pizza
     Hut slices priced under $7, other slice prices are left as reported).
     Items that are already multiple servings, personal/individual/kids'/whole
     pizzas, dessert pizzas, Gordita Crunches, and non-pizza "slice" items
     (cakes, pies, loaves) are left alone.
  3. Stamps every new meal with "_updated_at" (and "_added_at"), the date of this pull,
     and stamps meals.json itself ("updated_at"). products.html sorts by the newest
     "_updated_at" first, so the latest pull shows at the top of the Menu Items page.
  4. Writes everything to meals.json, which index.html and products.html read.

Which restaurants each run pulls: a run does NOT go through every restaurant. At the start
of each run the restaurants are sorted by how many meals meals.json already has for each,
fewest items first (never-pulled restaurants therefore come first), and the run works down
that list in order until it hits its call limit (MAX_CALLS_PER_RUN). The next run sorts
again from scratch, so whichever restaurants are thinnest at that moment go first. Nothing
is remembered between runs except the meals themselves. A restaurant whose call fails
(Gemini often answers 503 "overloaded") is retried once later in the same run; if it still
fails it simply stays near the front of the next run's sort.

Models and daily limits: Gemini Flash 3.8, 3.7, 3.6 and 3.5 are tried in that
order. Each model gets its own daily allowance (MAX_GEMINI_CALLS per model, default
20), counted on the Pacific-time day Google's quota resets on. When a model is out of
quota (or rejects the request), the script moves to the next model; a 503
("overloaded") first tries the next model too. Only answers Google actually served
count toward a model's allowance. Each run makes at most 20 Gemini calls
(MAX_CALLS_PER_RUN), so run it again to keep going.

Env var required: GEMINI_KEY  (missing -> the run is skipped, exit 0)
Optional env vars: MAX_GEMINI_CALLS (per model per day, default 20),
                   MAX_CALLS_PER_RUN (default 20)

Run locally:  GEMINI_KEY=xxxx python pull_meals.py
Dependencies: pip install requests
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
GEMINI_TIMEOUT_SECONDS = 240
GEMINI_MAX_RETRIES = 2            # network errors / 5xx only, per model per restaurant
GEMINI_RETRY_DELAY_SECONDS = 15
RETRY_PASS_DELAY_SECONDS = 60     # pause before the one extra pass over restaurants that failed

# Sanity bounds so a hallucinated number can't poison the meal planner.
MIN_CALORIES, MAX_CALORIES = 20, 3500
MIN_PRICE, MAX_PRICE = 0.50, 80.00

# The ONLY restaurants meals may come from. `site` = the restaurant's official website;
# its favicon is shown next to the delivery-app icons and links there. Shown on the web
# page too (index.html).
RESTAURANTS = [
    {"name": "Applebee's", "site": "www.applebees.com"},
    {"name": "Arby's", "site": "www.arbys.com"},
    {"name": "Blaze Pizza", "site": "www.blazepizza.com"},
    {"name": "Buffalo Wild Wings", "site": "www.buffalowildwings.com"},
    {"name": "Burger King", "site": "www.bk.com"},
    {"name": "Charleys Cheesesteaks", "site": "www.charleys.com"},
    {"name": "Cheddar's", "site": "www.cheddars.com"},
    {"name": "Chick-fil-A", "site": "www.chick-fil-a.com"},
    {"name": "Chili's", "site": "www.chilis.com"},
    {"name": "Chipotle", "site": "www.chipotle.com"},
    {"name": "Cracker Barrel", "site": "www.crackerbarrel.com"},
    {"name": "Culver's", "site": "www.culvers.com"},
    {"name": "Dairy Queen", "site": "www.dairyqueen.com"},
    {"name": "Denny's", "site": "www.dennys.com"},
    {"name": "Domino's", "site": "www.dominos.com"},
    {"name": "Dunkin'", "site": "www.dunkindonuts.com"},
    {"name": "Famous Dave's", "site": "www.famousdaves.com"},
    {"name": "Firehouse Subs", "site": "www.firehousesubs.com"},
    {"name": "Five Guys", "site": "www.fiveguys.com"},
    {"name": "Freddy's", "site": "www.freddys.com"},
    {"name": "Godfather's Pizza", "site": "www.godfathers.com"},
    {"name": "IHOP", "site": "www.ihop.com"},
    {"name": "Jersey Mike's Subs", "site": "www.jerseymikes.com"},
    {"name": "Jimmy John's", "site": "www.jimmyjohns.com"},
    {"name": "Kentucky Fried Chicken", "site": "www.kfc.com"},
    {"name": "Little Caesar's", "site": "www.littlecaesars.com"},
    {"name": "Long John Silver's", "site": "www.ljsilvers.com"},
    {"name": "McAlister's Deli", "site": "www.mcalistersdeli.com"},
    {"name": "McDonald's", "site": "www.mcdonalds.com"},
    {"name": "Moe's Southwest Grill", "site": "www.moes.com"},
    {"name": "Noodles & Company", "site": "www.noodles.com"},
    {"name": "Olive Garden", "site": "www.olivegarden.com"},
    {"name": "Outback Steakhouse", "site": "www.outback.com"},
    {"name": "Panda Express", "site": "www.pandaexpress.com"},
    {"name": "Panera", "site": "www.panerabread.com"},
    {"name": "Papa John's", "site": "www.papajohns.com"},
    {"name": "Papa Murphy's", "site": "www.papamurphys.com"},
    {"name": "Pizza Hut", "site": "www.pizzahut.com"},
    {"name": "Popeyes", "site": "www.popeyes.com"},
    {"name": "Qdoba", "site": "www.qdoba.com"},
    {"name": "Raising Cane's", "site": "www.raisingcanes.com"},
    {"name": "Red Robin", "site": "www.redrobin.com"},
    {"name": "Ruby Tuesday", "site": "www.rubytuesday.com"},
    {"name": "Slim Chickens", "site": "www.slimchickens.com"},
    {"name": "Sonic Drive-In", "site": "www.sonicdrivein.com"},
    {"name": "Starbucks", "site": "www.starbucks.com"},
    {"name": "Subway", "site": "www.subway.com"},
    {"name": "Taco Bell", "site": "www.tacobell.com"},
    {"name": "Taco John's", "site": "www.tacojohns.com"},
    {"name": "Texas Roadhouse", "site": "www.texasroadhouse.com"},
    {"name": "Wendy's", "site": "www.wendys.com"},
    {"name": "Wingstop", "site": "www.wingstop.com"},
]

RESTAURANT_NAMES = [r["name"] for r in RESTAURANTS]
RESTAURANT_SITES = {r["name"]: r["site"] for r in RESTAURANTS}   # official site: favicon + link on the web pages

CATEGORIES = ("breakfast", "regular", "dessert")

PROMPT = """You are building a menu database for a calorie-counting meal planner.

Restaurant: {restaurant}
This restaurant is a U.S. national chain.

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
- "healthy": true only if a reasonable dietitian would call it one of the healthier choices on this menu -- for example grilled or lean protein, salads and veggie-forward bowls, oatmeal, fruit, or a lower-calorie item that is not deep-fried and not heavy on sugar or cheese. Desserts, shakes, fried items and heavy combo-style items are normally false. Pizza is never healthy (always false). Tacos are not healthy either (false), with one exception: street-style tacos (small soft tortillas with grilled protein, onion, cilantro and the like, as at Chipotle) can be healthy.

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
    store.pop("pending", None)        # old "resume" list -- no longer used; every run re-sorts
    # Keep the published restaurant list (and sites) in the JSON in sync
    # with this script so the web page always shows what the script uses.
    for r in RESTAURANTS:
        entry = store["restaurants"].setdefault(r["name"], {})
        entry.pop("except_states", None)        # states are no longer tracked
        entry["site"] = r["site"]
        entry.setdefault("last_pulled", "")
    store["allowed_restaurants"] = RESTAURANT_NAMES
    unhealthied = apply_healthy_rules_to_store(store)
    if unhealthied:
        log(f"Healthy rules: un-flagged {unhealthied} saved pizza/taco meal(s).")
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


def build_prompt(restaurant, known_names):
    shown = known_names[-KNOWN_NAMES_SENT_PER_CALL:]
    known_block = "\n".join(f"- {n}" for n in shown) if shown else "(none yet)"
    return PROMPT.format(
        restaurant=restaurant,
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


_PIZZA_NOT_HEALTHY_RE = re.compile(r"\bpizzas?\b", re.I)
_TACO_RE = re.compile(r"\btacos?\b", re.I)
_STREET_TACO_RE = re.compile(r"\bstreet\b|\bfresco\b", re.I)   # street-style and Taco Bell Fresco tacos are fine
HEALTHY_TACO_RESTAURANTS = {"Chipotle", "Qdoba"}    # their tacos are fine, so Gemini's healthy flag stands


def apply_healthy_rules(meal):
    """Pizza is never healthy. Tacos aren't either, except street-style / Fresco tacos (anything with
    "street" or "fresco" in the name, or Chipotle's or Qdoba's). Returns True if the healthy flag was switched off."""
    if not meal.get("healthy"):
        return False
    name, serving = meal.get("name", ""), str(meal.get("serving", ""))
    not_healthy = bool(_PIZZA_NOT_HEALTHY_RE.search(name) or _PIZZA_NOT_HEALTHY_RE.search(serving))
    if not not_healthy and _TACO_RE.search(name):
        not_healthy = not (_STREET_TACO_RE.search(name) or meal.get("restaurant") in HEALTHY_TACO_RESTAURANTS)
    if not_healthy:
        meal["healthy"] = False
    return not_healthy


def apply_healthy_rules_to_store(store):
    """Pass over meals already saved in meals.json (harmless to repeat)."""
    return sum(1 for m in store["meals"] if apply_healthy_rules(m))


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
        "image_url": "",         # left empty on purpose; refresh_images.py fills it in
        "image_full_url": "",
    }
    apply_serving_rules(meal)   # single taco -> 3 tacos, single pizza slice -> 2 slices
    apply_healthy_rules(meal)   # no healthy pizza or (non-street) tacos
    return meal


#---------------------------------------------------------------------------
# Main
#---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default=OUTPUT_PATH, help="Path to meals.json")
    args = parser.parse_args()

    store = load_store(args.output)

    key = os.environ.get("GEMINI_KEY")
    if not key:
        log("No GEMINI_KEY set -- skipping the Gemini meal pull.")
    else:
        pull_meals(store, key, args.output)

    save_store(args.output, store)
    by_cat = {c: sum(1 for m in store["meals"] if m["category"] == c) for c in CATEGORIES}
    no_image = sum(1 for m in store["meals"] if not m.get("image_url"))
    log(f"Done. meals.json now has {len(store['meals'])} meals "
        f"({by_cat['breakfast']} breakfast, {by_cat['regular']} regular, {by_cat['dessert']} dessert); "
        f"{no_image} still need a picture (run refresh_images.py).")


def sort_fewest_items_first(store, names):
    """Order restaurant names by how many meals meals.json already holds for each,
    fewest first (ties keep their RESTAURANTS order), so thin restaurants fill up first."""
    counts = {}
    for m in store["meals"]:
        counts[m["restaurant"]] = counts.get(m["restaurant"], 0) + 1
    return sorted(names, key=lambda n: counts.get(n, 0))   # sorted() is stable


def pull_one(store, restaurant, key, limiter, known):
    """One Gemini call for one restaurant. Returns the number of meals added,
    or raises if the call failed."""
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
        meal["_updated_at"] = now     # products.html sorts newest first by this
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

    # Every run starts fresh: sort ALL restaurants by item count, fewest first (never-pulled
    # ones have 0 so they lead), then work down the list until the run's call limit.
    queue = sort_fewest_items_first(store, RESTAURANT_NAMES)
    log(f"Gemini: up to {budget} call(s) left today across {len(models_available(quota))} model(s). "
        f"Run order starts with: {', '.join(queue[:MAX_GEMINI_CALLS_PER_RUN])}.")

    known = {meal_key(m["restaurant"], m["name"]) for m in store["meals"]}
    limiter = _RateLimiter(MIN_CALL_INTERVAL_SECONDS)
    failed, retried = [], False
    pulled = []
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
                break                  # still failing; the next run's sort tries them again
            if successes >= len(RESTAURANT_NAMES):
                break                  # a run never needs to go around twice
            queue = sort_fewest_items_first(store, [n for n in RESTAURANT_NAMES if n not in pulled])
            if not queue:
                break
            continue

        restaurant = queue.pop(0)
        attempts += 1
        try:
            added = pull_one(store, restaurant, key, limiter, known)
        except QuotaExceeded as e:
            log(f"  {restaurant}: {e} -- stopping; the next run re-sorts and continues.")
            save_store(out_path, store)
            return
        except Exception as e:  # noqa: BLE001 -- one bad call shouldn't kill the run
            log(f"  {restaurant}: failed ({e}); it will be retried once, then left for the next run.")
            failed.append(restaurant)
            save_store(out_path, store)
            continue

        successes += 1
        added_total += added
        pulled.append(restaurant)
        save_store(out_path, store)    # progress survives an interrupted run

    log(f"Gemini: {successes} restaurant(s) pulled ({', '.join(pulled) or 'none'}), {added_total} new meal(s); "
        f"calls used today (Pacific) per model: {quota['calls']}. "
        f"The next run re-sorts by item count.")
    save_store(out_path, store)


if __name__ == "__main__":
    main()
