"""
Lazy Man's Fast Food Diet -- re-find EVERY meal picture on the restaurant's own site.

Run by hand (GitHub: Actions > "Refresh all meal images (manual)" > Run workflow),
or locally:   python refresh_images.py

For each meal it searches DuckDuckGo Images restricted to the restaurant's official
website, e.g.

    Olive Garden / Fettuccine Alfredo  ->  site:www.olivegarden.com Fettuccine Alfredo

(the sites are in RESTAURANTS in pull_meals.py) and replaces the meal's picture with
the first https result. If the official site has nothing for a meal, its current
picture is kept (a meal with no picture at all falls back to an open search when
IMAGE_FALLBACK_TO_WEB is on in pull_meals.py).

DuckDuckGo throttles fast searching (it answers with errors such as "malformed
headers"), so this goes slowly: a pause of --delay seconds plus a little random
extra between searches, and when a search fails it cools down (30s, 60s, 2 min,
4 min, 5 min) and retries the SAME meal. Only after several failures in a row does
it stop and save. Expect a full refresh of ~2,650 meals to take a few runs.

It resumes: every meal that has been searched gets "_image_site_checked": true, so a
run that stops early (throttling, the time budget) is simply run again and carries
on with the meals not done yet. Use --restart to begin again from the top.
New meals pulled by pull_meals.py are searched the same way and marked as done, so
this only needs to be run once for the existing list.

Options:
  --restaurant "Olive Garden"   only that restaurant (handy for a quick test)
  --limit 25                    stop after this many meals
  --restart                     forget what was already refreshed and do them all again
  --delay 3                     seconds between searches (plus 0-2s random); default 3
  --time-budget-minutes 100     stop (and save) after this long; default 60
  --output path/to/meals.json

Dependencies: pip install requests ddgs
"""

import argparse
import random
import sys
import time

import pull_meals as pm

SAVE_EVERY = 25            # write meals.json this often so an interrupted run keeps its progress
DEFAULT_DELAY_SECONDS = 3.0
JITTER_SECONDS = 2.0       # random extra pause so the searches aren't evenly spaced
MAX_STRIKES = 6            # failures in a row before the run stops (cooldowns total ~12 minutes)
COOLDOWN_BASE_SECONDS = 30
COOLDOWN_MAX_SECONDS = 300
GIVE_UP_AFTER_STOPPED_RUNS = 3   # a meal at which 3 separate runs had to stop is skipped (keeps its picture)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default=pm.OUTPUT_PATH, help="Path to meals.json")
    ap.add_argument("--restaurant", default="", help="Only this restaurant (exact name)")
    ap.add_argument("--limit", type=int, default=0, help="Stop after this many meals (0 = no limit)")
    ap.add_argument("--restart", action="store_true", help="Redo meals that were already refreshed")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Seconds between searches")
    ap.add_argument("--time-budget-minutes", type=float, default=pm.IMAGE_TIME_BUDGET_MINUTES)
    args = ap.parse_args()

    if pm.DDGS is None:
        raise SystemExit("ddgs isn't installed (pip install ddgs).")

    restaurant = args.restaurant.strip()
    if restaurant:
        match = [n for n in pm.RESTAURANT_NAMES if n.lower() == restaurant.lower()]
        if not match:
            raise SystemExit(f"Unknown restaurant {restaurant!r}. Choose one of: {', '.join(pm.RESTAURANT_NAMES)}")
        restaurant = match[0]

    store = pm.load_store(args.output)
    scope = [m for m in store["meals"] if not restaurant or m["restaurant"] == restaurant]
    if args.restart:
        for m in scope:
            m.pop("_image_site_checked", None)
            m.pop("_image_refresh_failures", None)
    todo = [m for m in scope if not m.get("_image_site_checked")]
    if args.limit > 0:
        todo = todo[: args.limit]

    label = restaurant or "all restaurants"
    pm.log(f"Refresh: {len(scope)} meal(s) for {label}; {len(todo)} still to do this run.")
    if not todo:
        pm.log("Nothing to do -- every meal has already been refreshed (use --restart to redo them).")
        return

    replaced = kept = added = done = 0
    strikes = 0
    stopped = False
    deadline = time.monotonic() + args.time_budget_minutes * 60
    try:
        for meal in todo:
            if time.monotonic() > deadline:
                pm.log(f"  time budget ({args.time_budget_minutes:g} min) reached -- run it again to continue.")
                break
            had_picture = bool(meal.get("image_url"))

            while True:                                  # retry this same meal after a cooldown
                try:
                    thumb, full, source = pm.find_image(meal["restaurant"], meal["name"], fallback=not had_picture)
                    strikes = 0
                    break
                except Exception as e:  # noqa: BLE001 -- DDG throws many error types when it throttles
                    strikes += 1
                    pm.log(f"  search failed ({strikes}/{MAX_STRIKES}) for {meal['restaurant']} / {meal['name']}: {e}")
                    if strikes >= MAX_STRIKES:
                        stopped = True
                        fails = meal["_image_refresh_failures"] = meal.get("_image_refresh_failures", 0) + 1
                        if fails >= GIVE_UP_AFTER_STOPPED_RUNS:
                            meal["_image_site_checked"] = True   # skip this one from now on; it keeps its picture
                            pm.log(f"  giving up on {meal['restaurant']} / {meal['name']} after {fails} stopped runs.")
                        break
                    cooldown = min(COOLDOWN_BASE_SECONDS * 2 ** (strikes - 1), COOLDOWN_MAX_SECONDS)
                    pm.log(f"  DuckDuckGo is throttling -- cooling down {cooldown}s, then retrying the same meal.")
                    time.sleep(cooldown)
            if stopped:
                pm.log("  too many failures in a row -- stopping and saving; run it again later to continue.")
                break

            meal["_image_site_checked"] = True
            meal.pop("_image_refresh_failures", None)
            if thumb:
                meal["image_url"] = thumb
                meal["image_full_url"] = full or ""
                meal["_image_source"] = source
                if had_picture:
                    replaced += 1
                else:
                    added += 1
            else:
                kept += 1                                    # nothing on the official site: keep what it had
            done += 1
            if done % SAVE_EVERY == 0:
                pm.log(f"  ...{done} done ({replaced} replaced, {added} added, {kept} kept as they were)")
                pm.save_store(args.output, store)
            time.sleep(args.delay + random.uniform(0, JITTER_SECONDS))
    finally:
        pm.save_store(args.output, store)

    left = sum(1 for m in scope if not m.get("_image_site_checked"))
    pm.log(f"Refresh finished: {done} meal(s) searched -- {replaced} picture(s) replaced, "
           f"{added} added, {kept} kept (nothing on the official site). {left} meal(s) left for {label}.")
    if left:
        pm.log("Run it again to continue where this left off.")


if __name__ == "__main__":
    sys.exit(main())
