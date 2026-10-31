"""
Lazy Man's Fast Food Diet -- availability checker (GitHub Actions: "Check meal availability (4x daily)").

Walks through every meal in meals.json and asks Gemini whether it is, as of TODAY, a regular
item sold on the restaurant's standard U.S. menu in most states / at most of its locations.
Each meal is marked:

    "active": true    -- sold in most states right now
    "active": false   -- discontinued, limited-time / seasonal and not on sale now, regional or
                         test-market only, only at a few locations, or the model isn't sure it exists

The site ignores these fields (nothing is hidden yet); they are for tracking and later filtering.

How a run works:

  * Meals are checked ITEMS_PER_CALL (40) at a time, one Gemini call per batch, up to
    CALLS_PER_RUN (20) calls per run = up to 800 meals per run. Batches go through the
    restaurants in order (the RESTAURANTS order in pull_meals.py), so a batch is almost always
    one restaurant.
  * The calls are spread round-robin over all four Flash models (3.8, 3.7, 3.6, 3.5), so each
    model gets about 5 calls per run. Each model is capped at MAX_GEMINI_CALLS (20) calls per
    Pacific-time day -- 4 runs x 5 calls = exactly 20 each. If a model is out of quota or
    keeps failing, the other models take its share.
  * CYCLES. The whole pool is one "cycle". Each meal remembers the cycle it was last checked in
    ("_avail_cycle"). A meal is pending until it has been checked in the current cycle. When
    nothing is pending, the cycle is finished: a summary goes into availability.history, the
    cycle number goes up by one, and every meal is pending again, so the whole pool is run
    through again from the top. New meals pulled by pull_meals.py join the current cycle.
  * PROGRESS. After every run the script reports "checked / pool" for the current cycle, with how
    many of those were marked active and inactive. It is printed in the log, written to the
    GitHub run summary, used in the commit message, and stored in meals.json under
    "availability" -> "progress" (plus "last_run" and "history").

Per meal fields written (bookkeeping, underscore ones don't touch "_updated_at" so the Menu
Items sort order does not change): "active", "_avail_cycle", "_avail_checked_at", "_avail_model".

Important limit: Gemini answers from what it learned in training. "As of today" means today's
date is put in the prompt, but a model cannot see menus that changed after its training. Set
AVAIL_USE_SEARCH=1 to turn on Gemini's Google Search grounding for better freshness (it has its
own quota and may not be allowed on every key/model; off by default).

Env vars: GEMINI_KEY (required; missing -> run skipped, exit 0)
Optional: AVAIL_CALLS_PER_RUN (default 20), AVAIL_ITEMS_PER_CALL (default 40),
          MAX_GEMINI_CALLS (per model per Pacific day, default 20), AVAIL_USE_SEARCH (0/1)

Run locally:  GEMINI_KEY=xxxx python check_availability.py
Dependencies: pip install requests   (imports helpers from pull_meals.py, keep it alongside)
"""

import argparse
import math
import os
import time
from datetime import datetime, timezone

import requests

import pull_meals as pm
from pull_meals import log

CALLS_PER_RUN = int(os.environ.get("AVAIL_CALLS_PER_RUN", "20"))
ITEMS_PER_CALL = int(os.environ.get("AVAIL_ITEMS_PER_CALL", "40"))
MAX_CALLS_PER_MODEL_PER_DAY = int(os.environ.get("MAX_GEMINI_CALLS", "20"))
USE_SEARCH = os.environ.get("AVAIL_USE_SEARCH", "0").strip().lower() in ("1", "true", "yes")

MIN_ANSWER_FRACTION = 0.9      # a reply that covers less than this share of the batch is treated as failed
FLAKY_AFTER = 3                # skip a model for the rest of the run after this many failures in a row
MAX_FAILED_BATCHES = 5         # stop the run if this many batches fail on every model
HISTORY_KEEP = 30

MODELS = pm.GEMINI_MODELS
RESTAURANT_ORDER = {n: i for i, n in enumerate(pm.RESTAURANT_NAMES)}

PROMPT = """Today's date is {today}.

You are checking a fast food menu database. For each numbered item below, decide whether it is
CURRENTLY (as of today's date) a regular item on the standard U.S. menu of the restaurant named
on its line, and sold in MOST STATES -- that is, at most of that chain's U.S. locations.

Mark "active": true ONLY if the item is on the regular menu right now and available in most states.

Mark "active": false if ANY of these is true:
- it has been discontinued or retired
- it is a limited-time, seasonal or promotional item that is not on sale today
- it is regional, test-market, or sold in only a few states or at only some locations
- it belongs to a different restaurant than the one named
- you are not reasonably confident that it exists and is sold today

Be strict: do not guess "true". The name may be slightly reworded; judge the real menu item.

ITEMS:
{items_block}

Return ONLY a JSON array with exactly one object per item, in the form {{"id": <number>, "active": true or false}}, and every id from 1 to {n} included once."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "active": {"type": "BOOLEAN"}},
        "required": ["id", "active"],
    },
}


class ModelUnavailable(Exception):
    """A model answered 4xx (quota used up / unknown model / bad request) -- skip it for today."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# State kept in meals.json under "availability"
# ---------------------------------------------------------------------------

def get_state(store):
    av = store.setdefault("availability", {})
    av.setdefault("cycle", 1)
    av.setdefault("history", [])
    q = av.setdefault("quota", {})
    today = pm.pacific_today()
    if q.get("date") != today:           # Google's daily quota resets on the Pacific-time day
        q.clear()
        q.update({"date": today, "calls": {}, "exhausted": []})
    q.setdefault("calls", {})
    q.setdefault("exhausted", [])
    return av


def models_available(quota, flaky):
    return [m for m in MODELS
            if m not in quota["exhausted"]
            and quota["calls"].get(m, 0) < MAX_CALLS_PER_MODEL_PER_DAY
            and flaky.get(m, 0) < FLAKY_AFTER]


def pool_of(store):
    """Meals the site actually uses: restaurant must be on the approved list."""
    return [m for m in store["meals"] if m.get("restaurant") in RESTAURANT_ORDER]


def pending_of(pool, cycle, skip=()):
    todo = [m for m in pool if m.get("_avail_cycle", 0) < cycle and id(m) not in skip]
    todo.sort(key=lambda m: (RESTAURANT_ORDER[m["restaurant"]], m.get("name", "").lower()))
    return todo


def tally(pool, cycle):
    done = [m for m in pool if m.get("_avail_cycle", 0) >= cycle]
    return {
        "cycle": cycle,
        "checked": len(done),
        "pool": len(pool),
        "active": sum(1 for m in done if m.get("active") is True),
        "inactive": sum(1 for m in done if m.get("active") is False),
    }


def close_cycle(av, pool):
    t = tally(pool, av["cycle"])
    av["history"].append({"cycle": t["cycle"], "completed_at": now_iso(), "pool": t["pool"],
                          "active": t["active"], "inactive": t["inactive"]})
    del av["history"][:-HISTORY_KEEP]
    log(f"Cycle {t['cycle']} COMPLETE: {t['checked']}/{t['pool']} checked, "
        f"{t['active']} active, {t['inactive']} inactive. Starting cycle {t['cycle'] + 1} over the whole pool.")
    av["cycle"] += 1
    return t


def progress_line(pool, av, cycle_complete):
    t = tally(pool, av["cycle"])
    pct = (100.0 * t["checked"] / t["pool"]) if t["pool"] else 0.0
    line = (f"Cycle {t['cycle']}: {t['checked']}/{t['pool']} meals checked ({pct:.1f}%) -- "
            f"{t['active']} active, {t['inactive']} inactive")
    if cycle_complete:
        line += f" -- CYCLE COMPLETE, next run starts cycle {t['cycle'] + 1} from the top"
    return line, t


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def build_prompt(batch, today):
    lines = []
    for i, m in enumerate(batch, 1):
        lines.append(f"{i}. {m['restaurant']} -- {m['name']} ({m.get('serving', '1 serving')}, "
                     f"{m.get('calories', '?')} cal)")
    return PROMPT.format(today=today, items_block="\n".join(lines), n=len(batch))


def ask_model(model, prompt, key, limiter, quota):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen = {"maxOutputTokens": 16384}
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
    if USE_SEARCH:
        body["tools"] = [{"google_search": {}}]        # grounded answers can't be forced into a JSON schema
    else:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = RESPONSE_SCHEMA

    last_err = None
    for attempt in range(pm.GEMINI_MAX_RETRIES):
        limiter.wait()
        try:
            resp = requests.post(url, headers={"x-goog-api-key": key}, json=body,
                                 timeout=pm.GEMINI_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            last_err = e
            log(f"  {model}: request error (attempt {attempt + 1}/{pm.GEMINI_MAX_RETRIES}): {e}")
            time.sleep(pm.GEMINI_RETRY_DELAY_SECONDS)
            continue
        if 400 <= resp.status_code < 500:
            raise ModelUnavailable(f"{resp.status_code} {resp.reason}: {resp.text[:300]}")
        if resp.status_code >= 500:
            last_err = RuntimeError(f"{resp.status_code} {resp.reason}")
            log(f"  {model}: {resp.status_code} (attempt {attempt + 1}/{pm.GEMINI_MAX_RETRIES})")
            time.sleep(pm.GEMINI_RETRY_DELAY_SECONDS)
            continue

        quota["calls"][model] = quota["calls"].get(model, 0) + 1     # Google served it: counts toward the day
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            return pm.extract_json_array(text)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  {model}: unusable response (attempt {attempt + 1}/{pm.GEMINI_MAX_RETRIES}): {e}")
            time.sleep(pm.GEMINI_RETRY_DELAY_SECONDS)
    raise last_err or RuntimeError(f"{model} failed for an unknown reason")


def parse_verdicts(items, n):
    """{id: bool} for ids 1..n; ignores malformed entries and repeats."""
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        a = it.get("active")
        if isinstance(a, str):
            a = {"true": True, "false": False}.get(a.strip().lower())
        if isinstance(a, bool) and 1 <= i <= n and i not in out:
            out[i] = a
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def check_run(store, key, out_path):
    av = get_state(store)
    quota = av["quota"]
    pool = pool_of(store)
    if not pool:
        log("No meals in the pool yet -- nothing to check.")
        return None

    today = datetime.now(pm.ZoneInfo("America/Los_Angeles")).strftime("%B %d, %Y")
    limiter = pm._RateLimiter(pm.MIN_CALL_INTERVAL_SECONDS)
    run_done = set()          # id() of meals handled (or given up on) this run: never revisited this run
    flaky = {}                # consecutive failures per model this run
    per_model = {}            # served calls per model this run
    calls = failed_batches = marked_active = marked_inactive = 0
    closed = []               # cycles that finished during this run

    log(f"Availability check, cycle {av['cycle']}: up to {CALLS_PER_RUN} calls x {ITEMS_PER_CALL} meals; "
        f"calls used today per model: {quota['calls']}; search grounding: {'on' if USE_SEARCH else 'off'}.")

    while calls < CALLS_PER_RUN and failed_batches < MAX_FAILED_BATCHES:
        models = models_available(quota, flaky)
        if not models:
            log("Every model is used up for today (or failing) -- stopping; the next run continues.")
            break

        if not pending_of(pool, av["cycle"]):          # whole pool checked this cycle -> start the next one
            closed.append(close_cycle(av, pool))
        todo = pending_of(pool, av["cycle"], run_done)
        if not todo:
            break                                      # only meals already handled this run remain
        batch = todo[:ITEMS_PER_CALL]

        start = calls % len(models)                    # round-robin over the models
        order = models[start:] + models[:start]
        prompt = build_prompt(batch, today)
        verdicts, used = None, None
        for model in order:
            if quota["calls"].get(model, 0) >= MAX_CALLS_PER_MODEL_PER_DAY:
                continue
            try:
                items = ask_model(model, prompt, key, limiter, quota)
                got = parse_verdicts(items, len(batch))
                if len(got) < math.ceil(MIN_ANSWER_FRACTION * len(batch)):
                    raise ValueError(f"only {len(got)} of {len(batch)} items answered")
                verdicts, used = got, model
                flaky[model] = 0
                break
            except ModelUnavailable as e:
                quota["exhausted"].append(model)
                log(f"  {model} unavailable for today ({e}) -- trying the next model.")
            except Exception as e:  # noqa: BLE001
                flaky[model] = flaky.get(model, 0) + 1
                log(f"  {model} failed ({e}) -- trying the next model.")

        run_done.update(id(m) for m in batch)          # never loop on the same batch within a run
        if verdicts is None:
            failed_batches += 1
            log(f"  Batch starting at {batch[0]['restaurant']} / {batch[0]['name']} failed on every model; "
                f"those meals stay pending for the next run.")
            pm.save_store(out_path, store)
            continue

        stamp = now_iso()
        a = b = 0
        for i, meal in enumerate(batch, 1):
            if i not in verdicts:
                continue                               # model skipped it: stays pending, retried next run
            meal["active"] = verdicts[i]
            meal["_avail_cycle"] = av["cycle"]
            meal["_avail_checked_at"] = stamp
            meal["_avail_model"] = used
            if verdicts[i]:
                a += 1
            else:
                b += 1
        calls += 1
        marked_active += a
        marked_inactive += b
        per_model[used] = per_model.get(used, 0) + 1
        names = sorted({m["restaurant"] for m in batch})
        log(f"  call {calls}/{CALLS_PER_RUN} [{used}] {', '.join(names)}: {a} active, {b} inactive "
            f"({len(batch) - a - b} unanswered).")
        pm.save_store(out_path, store)                 # progress survives an interrupted run

    complete = not pending_of(pool, av["cycle"])
    line, t = progress_line(pool, av, complete)
    for c in closed:
        line += (f" -- cycle {c['cycle']} finished this run ({c['active']} active, "
                 f"{c['inactive']} inactive of {c['pool']})")
    av["progress"] = {**t, "cycle_complete": complete, "updated_at": now_iso()}
    av["last_run"] = {"at": now_iso(), "calls": calls, "marked_active": marked_active,
                      "marked_inactive": marked_inactive, "calls_per_model": per_model}
    log(f"This run: {calls} call(s), {marked_active + marked_inactive} meals marked "
        f"({marked_active} active, {marked_inactive} inactive); calls today per model: {quota['calls']}.")
    return line, av, pool


def publish_progress(line, av, pool):
    print(f"PROGRESS: {line}", flush=True)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"progress={line}\n")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        t = av["progress"]
        lr = av["last_run"]
        flags = {True: 0, False: 0, None: 0}
        for m in pool:
            flags[m.get("active")] += 1
        md = [
            "## Meal availability check",
            f"**{line}**",
            "",
            "| | |",
            "|---|---|",
            f"| Cycle | {t['cycle']} |",
            f"| Checked this cycle | {t['checked']} / {t['pool']} |",
            f"| Marked active this cycle | {t['active']} |",
            f"| Marked inactive this cycle | {t['inactive']} |",
            f"| This run | {lr['marked_active']} active, {lr['marked_inactive']} inactive in {lr['calls']} call(s) |",
            f"| Whole pool right now | {flags[True]} active, {flags[False]} inactive, {flags[None]} never checked |",
            f"| Gemini calls today per model | {av['quota']['calls']} |",
            "",
        ]
        if av["history"]:
            md.append("Completed cycles: " + "; ".join(
                f"#{h['cycle']} ({h['active']} active / {h['inactive']} inactive of {h['pool']})"
                for h in av["history"][-5:]))
        with open(summ, "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default=pm.OUTPUT_PATH, help="Path to meals.json")
    args = parser.parse_args()

    key = os.environ.get("GEMINI_KEY")
    if not key:
        log("No GEMINI_KEY set -- skipping the availability check.")
        return
    store = pm.load_store(args.output)
    result = check_run(store, key, args.output)
    pm.save_store(args.output, store)
    if result:
        publish_progress(*result)


if __name__ == "__main__":
    main()
