# Lazy Man's Fast Food Diet

A static website that builds a 7-day meal plan, **only from 43 national fast food chains**, to hit a daily calorie target. Menu data (calories, price, serving, healthy/vegetarian flags) is gathered by Google Gemini, and pictures come from DuckDuckGo Images. It is a **two-step manual process**: you run two GitHub Actions by hand, first one to pull meals, then one to fill in their pictures (see "How to update the data").

> All calories, prices and servings are **AI estimates**. Menus and prices vary by location, so double-check at the restaurant.

## Files

| File | What it does |
|---|---|
| `index.html` | The home page: enter weight/age/activity, get a 7-day plan, swap meals, save weeks, order links. |
| `products.html` | "Menu Items": a searchable table of every meal in the data, and the picker used by the home page's **select** button. |
| `pull_meals.py` | **Step 1.** Asks Gemini for menu items and writes them to `meals.json` with an **empty** picture. No image search. |
| `.github/workflows/pull_meals.yml` | Manual GitHub Action ("Pull fast food meals (manual)") that runs `pull_meals.py` and commits `meals.json`. |
| `refresh_images.py` | **Step 2.** Finds pictures on DuckDuckGo, **only for meals whose picture is empty** (see "Pictures"). |
| `.github/workflows/refresh_images.yml` | Manual GitHub Action ("Fill missing meal images (manual)") that runs `refresh_images.py` and commits `meals.json`. |
| `meals.json` | **Created by the first run.** All meals plus run state. Both pages read it. |

Repo layout:

```
index.html
products.html
pull_meals.py
refresh_images.py
.github/workflows/pull_meals.yml
.github/workflows/refresh_images.yml
meals.json            <- created by the first run
```

## Setup

1. Put the files in a GitHub repo as shown above.
2. Get a Gemini API key (Google AI Studio) and add it as a repository secret named **`GEMINI_KEY`** (Settings, Secrets and variables, Actions).
3. Turn on GitHub Pages for the repo (Settings, Pages) so `index.html` is served.
4. Go to the **Actions** tab, open **Pull fast food meals (manual)** and click **Run workflow** once. This creates `meals.json`. Until it exists the site shows "Meal list not found yet."

Nothing runs on a schedule. You start both Actions yourself.

## How to update the data (two manual steps)

1. **Pull meals.** Actions tab, **Pull fast food meals (manual)**, Run workflow. New meals are added with an empty picture (`image_url` and `image_full_url` are `""`) and stamped with today's date. A full pass over all the restaurants can take more than one run; run it again to keep going.
2. **Fill pictures.** Actions tab, **Fill missing meal images (manual)**, Run workflow. It searches DuckDuckGo only for meals whose picture is empty, so existing pictures are never touched. DuckDuckGo throttles, so a big batch can take several runs; each run picks up the meals that are still empty.

**Date updated.** Every meal has an `_updated_at` timestamp. Step 1 sets it on each new meal, and step 2 sets it again on each meal that gets a picture. `meals.json` also has a top-level `updated_at` that is refreshed every time either script saves. **Menu Items** (`products.html`) sorts by `_updated_at`, newest first, so the most recent pull or picture fill is at the top. Meals from before this field existed fall back to `_added_at`.

## The restaurants

Meals can only come from these 43 chains. The official site is where each restaurant's favicon link on the page goes.

| Restaurant | Official site |
|---|---|
| Applebee's | [www.applebees.com](https://www.applebees.com/) |
| Arby's | [www.arbys.com](https://www.arbys.com/) |
| Blaze Pizza | [www.blazepizza.com](https://www.blazepizza.com/) |
| Buffalo Wild Wings | [www.buffalowildwings.com](https://www.buffalowildwings.com/) |
| Burger King | [www.bk.com](https://www.bk.com/) |
| Charleys Cheesesteaks | [www.charleys.com](https://www.charleys.com/) |
| Chick-fil-A | [www.chick-fil-a.com](https://www.chick-fil-a.com/) |
| Chili's | [www.chilis.com](https://www.chilis.com/) |
| Chipotle | [www.chipotle.com](https://www.chipotle.com/) |
| Cracker Barrel | [www.crackerbarrel.com](https://www.crackerbarrel.com/) |
| Culver's | [www.culvers.com](https://www.culvers.com/) |
| Dairy Queen | [www.dairyqueen.com](https://www.dairyqueen.com/) |
| Denny's | [www.dennys.com](https://www.dennys.com/) |
| Domino's | [www.dominos.com](https://www.dominos.com/) |
| Dunkin' | [www.dunkindonuts.com](https://www.dunkindonuts.com/) |
| Firehouse Subs | [www.firehousesubs.com](https://www.firehousesubs.com/) |
| Five Guys | [www.fiveguys.com](https://www.fiveguys.com/) |
| Freddy's | [www.freddys.com](https://www.freddys.com/) |
| Godfather's Pizza | [www.godfathers.com](https://www.godfathers.com/) |
| IHOP | [www.ihop.com](https://www.ihop.com/) |
| Jersey Mike's Subs | [www.jerseymikes.com](https://www.jerseymikes.com/) |
| Jimmy John's | [www.jimmyjohns.com](https://www.jimmyjohns.com/) |
| Kentucky Fried Chicken | [www.kfc.com](https://www.kfc.com/) |
| Little Caesar's | [www.littlecaesars.com](https://www.littlecaesars.com/) |
| McAlister's Deli | [www.mcalistersdeli.com](https://www.mcalistersdeli.com/) |
| McDonald's | [www.mcdonalds.com](https://www.mcdonalds.com/) |
| Olive Garden | [www.olivegarden.com](https://www.olivegarden.com/) |
| Outback Steakhouse | [www.outback.com](https://www.outback.com/) |
| Panda Express | [www.pandaexpress.com](https://www.pandaexpress.com/) |
| Panera | [www.panerabread.com](https://www.panerabread.com/) |
| Papa John's | [www.papajohns.com](https://www.papajohns.com/) |
| Papa Murphy's | [www.papamurphys.com](https://www.papamurphys.com/) |
| Pizza Hut | [www.pizzahut.com](https://www.pizzahut.com/) |
| Popeyes | [www.popeyes.com](https://www.popeyes.com/) |
| Qdoba | [www.qdoba.com](https://www.qdoba.com/) |
| Red Robin | [www.redrobin.com](https://www.redrobin.com/) |
| Sonic Drive-In | [www.sonicdrivein.com](https://www.sonicdrivein.com/) |
| Starbucks | [www.starbucks.com](https://www.starbucks.com/) |
| Subway | [www.subway.com](https://www.subway.com/) |
| Taco Bell | [www.tacobell.com](https://www.tacobell.com/) |
| Texas Roadhouse | [www.texasroadhouse.com](https://www.texasroadhouse.com/) |
| Wendy's | [www.wendys.com](https://www.wendys.com/) |
| Wingstop | [www.wingstop.com](https://www.wingstop.com/) |

The list lives in `RESTAURANTS` at the top of `pull_meals.py` and is copied into `meals.json`, which is what the page's "Where the meals come from" section shows. The pages also ignore any meal whose restaurant isn't on that list.

## How the data is pulled (`pull_meals.py`)

**Gemini, one restaurant per call.** Each call asks for up to 60 new items for one restaurant. The names already on file are sent along so Gemini only returns new ones; the list grows a little every run. Each item comes back with:

- `category`: `breakfast`, `regular` or `dessert` (desserts, slushes, shakes, malts, sweet blended drinks)
- `calories`, `price`, `serving`
- `vegetarian` and `healthy` (yes/no, set by Gemini; desserts are never healthy)

Beverages, dips, sauces and condiments are excluded, and any item with an unknown category or an out-of-range calorie/price value is dropped.

**Serving rules (tacos and pizza slices).** Gemini reports a single taco or a single slice, and `pull_meals.py` converts it when saving (`apply_serving_rules`):

- A single taco (serving `1 taco`) is saved as **3 tacos**, with calories and price x3.
- A single pizza slice (serving `1 slice ...` on an item that is a pizza) is saved as **2 slices**, with calories x2. The price is doubled only for **Pizza Hut** slices under $7 (`SLICE_PRICE_DOUBLED_FOR`, `SLICE_PRICE_DOUBLE_BELOW`); other chains list a whole-pizza price on a one-slice item, so their prices are left as reported. Fractions are scaled too, e.g. `1 slice (1/8 pizza)` becomes `2 slices (2/8 pizza)`.
- Left alone: dessert pizzas (Dairy Queen Treatzza), Gordita Crunches, anything that is already several servings (`3 tacos`, `order of 3 tacos`, `2 rolled tacos`, `2 slices`, `1/4 pizza (2 slices)`), personal / individual / kids' / whole pizzas (`1 personal pizza`, `1 pizza (4 slices)`), combos (`1 slice pizza + 2 stix`), and "slices" of things that are not pizza (cheesecake, pie, loaves, ham).
- A leading "Single " is dropped from the name (`Single Chicken Taco` becomes `Chicken Taco`).
- Converted meals get `_serving_multiplier` (3 or 2). The rule skips meals that have it, and it also runs over `meals.json` every time the script starts, so saved meals can never be multiplied twice.

**Models and daily limits.** Gemini Flash **3.8, 3.7, 3.6, 3.5** are tried in that order. Each model has its own allowance of 20 calls per Pacific-time day (`MAX_GEMINI_CALLS`), and each run makes at most 20 calls (`MAX_CALLS_PER_RUN`), so a full pass over all the restaurants can take more than one run. Restaurants that have never been pulled (for example ones just added to `RESTAURANTS`) go to the front of the queue. Only answers Google actually served count toward the allowance.

- A model that answers with a 4xx (out of quota, unknown model ID) is skipped for the rest of that day.
- A model that returns 503/errors makes that restaurant fall through to the next model. After 3 failures in a row the model is skipped for the rest of the run and tried again on the next run.

**Resuming.** `meals.json` keeps a `pending` list of restaurants not yet pulled in the current pass. A restaurant leaves the list only after a successful pull, so the next run starts exactly where the last one stopped. When the list empties, a new pass begins.

**Pictures are not pulled here.** `pull_meals.py` does no image search at all. Every new meal is saved with `"image_url": ""` and `"image_full_url": ""`, and the Menu Items page shows "no image" until step 2 fills it in.

**Filling pictures (`refresh_images.py`).** To find pictures, open the **Actions** tab, choose **Fill missing meal images (manual)** and click **Run workflow** (or run `python refresh_images.py` locally). It only looks at meals whose `image_url` is empty. For each one it searches DuckDuckGo Images with the restaurant and the item **each in double quotes**, exactly as you would type it in the search box, and stores the first https image URL (`image_url`, plus the full-size `image_full_url` as a fallback). Olive Garden's Fettuccine Alfredo is searched as:

```
"Olive Garden" "Fettuccine Alfredo"
```

If that finds nothing, it tries again without any `(...)` in the item name (`"Chipotle" "Chicken Taco"` for `Chicken Taco (Soft Flour)`), then one looser search that keeps only the restaurant in quotes (turn that off with `IMAGE_FALLBACK_TO_WEB = False`). Each picture records which search found it in `_image_source`: `quoted` or `web` (the looser fallback). A meal that gets a picture also gets a new `_updated_at`. A meal where nothing was found stays empty and is tried again on later runs, up to 3 times (`_image_attempts`, `IMAGE_MAX_ATTEMPTS_PER_MEAL`); the **retry_failed** input gives those another go.

DuckDuckGo throttles fast searching, so the script pauses a few seconds between searches and, when a search fails, cools down (30 s up to 5 min) and retries the same meal; only after 6 failures in a row does it stop and save. There is no cap on the number of searches, only the time budget (default 110 minutes in the Action). Nothing is lost when a run stops early: just run it again and it continues with the meals that are still empty. Inputs: *retry_failed*, *restaurant* (just one chain, good for a quick test), *delay* (seconds between searches), *time budget*.

### Running it locally

```bash
pip install requests ddgs
GEMINI_KEY=your_key python pull_meals.py      # step 1: pull meals (empty pictures)
python refresh_images.py                      # step 2: fill the empty pictures
```

`pull_meals.py` option: `--output path/to/meals.json`. (`requests` is all it needs; `ddgs` is only for step 2.)

`refresh_images.py` options: `--restaurant "Olive Garden"`, `--limit 25`, `--retry-failed`, `--delay 3`, `--time-budget-minutes 100`, `--output path/to/meals.json`.

Environment variables: `GEMINI_KEY` (required for step 1), `MAX_GEMINI_CALLS` (per model per day, default 20), `MAX_CALLS_PER_RUN` (default 20).

### `meals.json` shape

```json
{
  "updated_at": "...",
  "allowed_restaurants": ["Applebee's", "..."],
  "restaurants": { "Chick-fil-A": { "site": "www.chick-fil-a.com", "last_pulled": "..." } },
  "quota": { "date": "2026-10-16", "calls": { "gemini-3.8-flash": 7 }, "exhausted": [] },
  "pending": ["Five Guys", "..."],
  "meals": [
    {
      "restaurant": "McDonald's", "name": "Big Mac", "category": "regular",
      "calories": 590, "price": 6.49, "serving": "1 sandwich",
      "vegetarian": false, "healthy": false,
      "image_url": "https://...", "image_full_url": "https://...",
      "_updated_at": "2026-10-18T14:48:08+00:00"
    }
  ]
}
```

Fields that start with an underscore (`_added_at`, `_updated_at`, `_model`, `_image_attempts`, `_image_source`, `_serving_multiplier`; older files may also have `_image_refreshed`, which is no longer used) are bookkeeping. If you ever want a clean start, delete `meals.json` and run the workflow again.

## How the plan works (`index.html`)

- Enter weight, age and activity level to get a daily calorie target (or type your own). Each day lands within 250 calories of the target.
- **Meals per day:** breakfast, lunch and dinner, plus a **late night** meal when the target is high enough. Late night always comes from the **dessert** category.
  - Normal: 3 meals up to 2800 calories, 4 meals above 2800.
  - With **Healthy** checked: 3 meals up to 2300 calories, 4 meals above 2300.
- Breakfast comes from the breakfast category; lunch and dinner from the regular category.
- **Filters:** *Max per meal ($)*, *Healthy* and *Vegetarian*. If a filter would leave too few meals, the plan falls back to the unfiltered pool and says so. Desserts (late night) are only affected by the price cap and the vegetarian filter.
- **Select** opens `products.html` to hand-pick a meal for a slot; **regen** swaps in a random meal from the same pool within 150 calories either way of the slot's original meal, however many times you press it (if nothing is that close, the closest one; `REGEN_CALORIE_RANGE` in `index.html`). Hand-picking with **select** makes the picked meal the new original.
- Each meal has icon links, squeezed together: first the **restaurant's own website** (its favicon links to the official site from the table above), then **DoorDash, Uber Eats and Grubhub**, which search for the restaurant name. All icons are the sites' favicons, loaded from Google's favicon service.
- Weeks can be saved as tabs. They live in your browser only.

## Customizing

| What | Where |
|---|---|
| Restaurants and their official sites (`site`) | `RESTAURANTS` in `pull_meals.py` (and the fallback copies near `restaurantInfo` in `index.html` and `restaurantSites` in `products.html`) |
| Models and per-day limits | `GEMINI_MODELS`, `MAX_GEMINI_CALLS_PER_DAY`, `MAX_GEMINI_CALLS_PER_RUN` in `pull_meals.py` |
| How many new items per Gemini call | `MAX_NEW_MEALS_PER_CALL` in `pull_meals.py` |
| Calorie/price sanity bounds | `MIN_CALORIES`, `MAX_CALORIES`, `MIN_PRICE`, `MAX_PRICE` in `pull_meals.py` |
| 3 vs 4 meals thresholds | `getMealSlotsForTarget` in `index.html` |
| Header picture | `--hero-photo` in the CSS of both HTML files: a photo of a fast food drive-thru ordering menu ("Digital Menu Boards in the Drive-Thru" by Erik Mclean on Pexels; free to use under the Pexels license, no credit required). Swap in any image URL you have rights to use. If the photo can't load, `--hero-fallback` (an inline illustration) shows instead. |

## Notes and limits

- Gemini's menu knowledge can be wrong or out of date. Numbers are estimates, not live menu data.
- Pictures come from DuckDuckGo searches for the quoted restaurant and item (step 2), but they aren't verified, so a picture may not match the item exactly (menus change and some items have no good photo online).
- Delivery-app search links go to each app's search page; results depend on your location.
- DuckDuckGo rate-limits aggressively, so a big backlog of pictures fills in over several runs of the image Action. Until then those meals show "no image".
