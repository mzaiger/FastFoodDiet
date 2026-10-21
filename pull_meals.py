"""
Lazy Man's Fast Food Diet -- fill in the meal pictures that are still EMPTY.

STEP 2 of a two-step manual process:
  1. "Pull fast food meals (manual)"   -> pull_meals.py     adds new meals with an empty picture
  2. "Fill missing meal images (manual)" -> refresh_images.py  finds pictures for the empty ones

Run by hand (GitHub: Actions > "Fill missing meal images (manual)" > Run workflow),
or locally:   python refresh_images.py

It ONLY touches meals whose image_url is empty. Meals that already have a picture are
never searched and never changed. For each empty meal it searches DuckDuckGo Images
with the restaurant and the item in double quotes, exactly as you would type it in the
search box:

    Olive Garden / Fettuccine Alfredo  ->  "Olive Garden" "Fettuccine Alfredo"

and stores the first https result. If that finds nothing it tries the same search
without any "(...)" in the item name (e.g. "Chicken Taco" for "Chicken Taco (Soft
Flour)"), and finally one looser search that keeps only the restaurant in quotes
(IMAGE_FALLBACK_TO_WEB). If there is still nothing the meal stays empty.

Every meal that gets a picture also gets a new "_updated_at" date, so it moves up on
the Menu Items page (products.html sorts newest "_updated_at" first). meals.json's own
"updated_at" is refreshed on every save.

DuckDuckGo throttles fast searching (it answers with errors such as "malformed
headers"), so this goes slowly: a pause of --delay seconds plus a little random
extra between searches, and when a search fails it cools down (30s, 60s, 2 min,
4 min, 5 min) and retries the SAME meal. Only after several failures in a row does
it stop and save.

It resumes by itself: a meal that got a picture is no longer empty, so a run that stops
early (throttling, the time budget) is simply run again and carries on with the meals
that are still empty. A meal where nothing was found is tried up to
IMAGE_MAX_ATTEMPTS_PER_MEAL times over separate runs (counted in "_image_attempts"),
then skipped; --retry-failed gives those another go.

Options:
  --restaurant "Olive Garden"   only that restaurant (handy for a quick test)
  --limit 25                    stop after this many meals
  --retry-failed                also retry meals that already used up their attempts
  --delay 3                     seconds between searches (plus 0-2s random); default 3
  --time-budget-minutes 100     stop (and save) after this long; default 60
  --output path/to/meals.json

Dependencies: pip install requests ddgs
"""

import argparse
import random
import re
import sys
import time
from datetime import datetime, timezone

import pull_meals as pm

try:  # the package was renamed from duckduckgo_search to ddgs
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

# Which ddgs image engine to use. ddgs's default ("auto") shuffles its engines and picks one
# at random for every search, so the same query can return different pictures from one meal
# to the next (that is where the odd photos came from). Pinning it makes every search
# come from the same place. Options: "bing" or "duckduckgo" (what duckduckgo.com/images
# shows by hand; it fails with "malformed headers" on GitHub's runners). Override for one
# run with --engine.
IMAGE_ENGINE = "bing"

IMAGE_SEARCH_DELAY_SECONDS = 1.5   # pause between the searches made for the same meal
IMAGE_MAX_ATTEMPTS_PER_MEAL = 3    # runs in which nothing was found, then the meal is skipped
# If the quoted searches find nothing, True falls back to a looser search
# ("<restaurant>" <item>, restaurant still quoted); False leaves the meal empty.
IMAGE_FALLBACK_TO_WEB = True

DEFAULT_TIME_BUDGET_MINUTES = 60
LOG_FIRST_URLS = 100000    # print this many pictures found (engine, query, url); huge = log every one
SAVE_EVERY = 25            # write meals.json this often so an interrupted run keeps its progress
DEFAULT_DELAY_SECONDS = 3.0
JITTER_SECONDS = 2.0       # random extra pause so the searches aren't evenly spaced
MAX_STRIKES = 6            # failures in a row before the run stops (cooldowns total ~12 minutes)
COOLDOWN_BASE_SECONDS = 30
COOLDOWN_MAX_SECONDS = 300
GIVE_UP_AFTER_STOPPED_RUNS = 3   # a meal at which 3 separate runs had to stop is skipped


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


def _first_https_image(query, engine=None):
    """One image search on the chosen engine. Returns (thumbnail_url, full_url) or (None, None)."""
    try:
        results = _client().images(query, max_results=6, safesearch="moderate",
                                   backend=engine or IMAGE_ENGINE)
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
    "(...)" in the item name, then (if allowed) a looser search."""
    queries = [(f"{_quote(restaurant)} {_quote(name)}", "quoted")]
    short = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if short and short.lower() != name.strip().lower():
        queries.append((f"{_quote(restaurant)} {_quote(short)}", "quoted"))
    if fallback:
        # Last resort. Keep the restaurant in quotes so the results stay on that chain;
        # a fully unquoted search ("Chili's Classic Sirloin (6 oz)") returns random junk.
        queries.append((f"{_quote(restaurant)} {short or name}", "web"))
    return queries


def find_image(restaurant, name, fallback=None, engine=None):
    """Searches images with the restaurant and item in double quotes on the pinned engine.
    Returns (thumbnail, full, source, query) -- source is "quoted" or "web" (looser fallback),
    query is the exact search that found it -- or (None, None, None, None) when nothing was found."""
    if fallback is None:
        fallback = IMAGE_FALLBACK_TO_WEB
    for i, (query, source) in enumerate(image_queries(restaurant, name, fallback)):
        if i:
            time.sleep(IMAGE_SEARCH_DELAY_SECONDS)       # pause between searches for the same meal
        thumb, full = _first_https_image(query, engine)
        if thumb:
            return thumb, full, source, query
    return None, None, None, None


#---------------------------------------------------------------------------
# Main
#---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default=pm.OUTPUT_PATH, help="Path to meals.json")
    ap.add_argument("--restaurant", default="", help="Only this restaurant (exact name)")
    ap.add_argument("--limit", type=int, default=0, help="Stop after this many meals (0 = no limit)")
    ap.add_argument("--engine", default=IMAGE_ENGINE, help=f"ddgs image engine (default {IMAGE_ENGINE}); e.g. duckduckgo or bing")
    ap.add_argument("--retry-failed", action="store_true", help="Also retry meals that used up their attempts")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Seconds between searches")
    ap.add_argument("--time-budget-minutes", type=float, default=DEFAULT_TIME_BUDGET_MINUTES)
    args = ap.parse_args()

    if DDGS is None:
        raise SystemExit("ddgs isn't installed (pip install ddgs).")
    engine = args.engine.strip().lower() or IMAGE_ENGINE

    restaurant = args.restaurant.strip()
    if restaurant:
        match = [n for n in pm.RESTAURANT_NAMES if n.lower() == restaurant.lower()]
        if not match:
            raise SystemExit(f"Unknown restaurant {restaurant!r}. Choose one of: {', '.join(pm.RESTAURANT_NAMES)}")
        restaurant = match[0]

    store = pm.load_store(args.output)
    scope = [m for m in store["meals"] if not restaurant or m["restaurant"] == restaurant]
    if args.retry_failed:
        for m in scope:
            if not m.get("image_url"):
                m["_image_attempts"] = 0
                m.pop("_image_refresh_failures", None)

    def needs_image(m):
        return not m.get("image_url") and m.get("_image_attempts", 0) < IMAGE_MAX_ATTEMPTS_PER_MEAL

    empty_total = sum(1 for m in scope if not m.get("image_url"))
    todo = [m for m in scope if needs_image(m)]
    if args.limit > 0:
        todo = todo[: args.limit]

    label = restaurant or "all restaurants"
    pm.log(f"Image engine: {engine} (ddgs backend={engine!r}; ddgs would otherwise pick one at random)")
    pm.log(f"Images: {empty_total} meal(s) with an empty picture for {label}; {len(todo)} to search this run.")
    if not todo:
        pm.log("Nothing to do -- no empty pictures (or the rest used up their attempts; use --retry-failed).")
        return

    found = not_found = done = 0
    logged_urls = 0
    strikes = 0
    stopped = False
    deadline = time.monotonic() + args.time_budget_minutes * 60
    try:
        for meal in todo:
            if time.monotonic() > deadline:
                pm.log(f"  time budget ({args.time_budget_minutes:g} min) reached -- run it again to continue.")
                break

            while True:                                  # retry this same meal after a cooldown
                try:
                    thumb, full, source, query = find_image(meal["restaurant"], meal["name"], engine=engine)
                    strikes = 0
                    break
                except Exception as e:  # noqa: BLE001 -- DDG throws many error types when it throttles
                    strikes += 1
                    pm.log(f"  search failed ({strikes}/{MAX_STRIKES}) for {meal['restaurant']} / {meal['name']}: {e}")
                    if strikes >= MAX_STRIKES:
                        stopped = True
                        fails = meal["_image_refresh_failures"] = meal.get("_image_refresh_failures", 0) + 1
                        if fails >= GIVE_UP_AFTER_STOPPED_RUNS:
                            meal["_image_attempts"] = IMAGE_MAX_ATTEMPTS_PER_MEAL   # skip this one from now on
                            pm.log(f"  giving up on {meal['restaurant']} / {meal['name']} after {fails} stopped runs.")
                        break
                    cooldown = min(COOLDOWN_BASE_SECONDS * 2 ** (strikes - 1), COOLDOWN_MAX_SECONDS)
                    pm.log(f"  DuckDuckGo is throttling -- cooling down {cooldown}s, then retrying the same meal.")
                    time.sleep(cooldown)
            if stopped:
                pm.log("  too many failures in a row -- stopping and saving; run it again later to continue.")
                break

            meal["_image_attempts"] = meal.get("_image_attempts", 0) + 1
            meal.pop("_image_refresh_failures", None)
            if thumb:
                meal["image_url"] = thumb
                meal["image_full_url"] = full or ""
                meal["_image_source"] = source           # "quoted" or "web" (looser fallback)
                meal["_image_engine"] = engine           # which engine found it, to trace odd pictures
                meal["_updated_at"] = datetime.now(timezone.utc).isoformat()
                found += 1
                if logged_urls < LOG_FIRST_URLS:
                    logged_urls += 1
                    pm.log(f"  image {logged_urls}: {meal['restaurant']} / {meal['name']} "
                           f"[engine={engine} {source}] {query} -> {thumb}")
            else:
                not_found += 1                           # stays empty; tried again on a later run
            done += 1
            if done % SAVE_EVERY == 0:
                pm.log(f"  ...{done} searched ({found} pictures found, {not_found} not found)")
                pm.save_store(args.output, store)
            time.sleep(args.delay + random.uniform(0, JITTER_SECONDS))
    finally:
        pm.save_store(args.output, store)

    left = sum(1 for m in scope if not m.get("image_url"))
    pm.log(f"Images finished: {done} meal(s) searched -- {found} picture(s) added, "
           f"{not_found} not found. {left} meal(s) still have an empty picture for {label}.")
    if left:
        pm.log("Run it again to continue where this left off.")


if __name__ == "__main__":
    sys.exit(main())
