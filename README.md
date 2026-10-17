# Lazy Man's Fast Food Diet

A static website that builds a 7-day meal plan, **only from 28 national fast food chains**, to hit a daily calorie target. Menu data (calories, price, serving, healthy/vegetarian flags) is gathered automatically by Google Gemini, and pictures come from DuckDuckGo Images. A GitHub Action refreshes it 4 times a day.

> All calories, prices and servings are **AI estimates**. Menus and prices vary by location, so double-check at the restaurant.

## Files

| File | What it does |
|---|---|
| `index.html` | The home page: enter weight/age/activity, get a 7-day plan, swap meals, save weeks, order links. |
| `products.html` | "Menu Items": a searchable table of every meal in the data, and the picker used by the home page's **select** button. |
| `pull_meals.py` | Asks Gemini for menu items, finds pictures on DuckDuckGo, and writes `meals.json`. |
| `.github/workflows/pull_meals.yml` | GitHub Action that runs `pull_meals.py` 4 times a day and commits `meals.json`. |
| `refresh_images.py` | Manual tool: redoes **every** meal picture with a quoted DuckDuckGo search (see "Pictures"). |
| `.github/workflows/refresh_images.yml` | Manual GitHub Action (Run workflow button) that runs `refresh_images.py` and commits `meals.json`. |
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
4. Go to the **Actions** tab, open **Pull fast food meals (4x daily)** and click **Run workflow** once. This creates `meals.json`. Until it exists the site shows "Meal list not found yet."

After that the Action runs on its own at 03:17, 09:17, 15:17 and 21:17 UTC.

## The restaurants

Meals can only come from these 28 chains: national chains with locations in at least about 90% of the states (45 or more). The states each one skips are noted below, and the official site is where the page's favicon link goes.

| Restaurant | States | Official site |
|---|---|---|
| Applebee's | all 50 | [www.applebees.com](https://www.applebees.com/) |
| Arby's | all except Rhode Island and Vermont | [www.arbys.com](https://www.arbys.com/) |
| Buffalo Wild Wings | all 50 | [www.buffalowildwings.com](https://www.buffalowildwings.com/) |
| Burger King | all 50 | [www.bk.com](https://www.bk.com/) |
| Chick-fil-A | all except Vermont and Alaska | [www.chick-fil-a.com](https://www.chick-fil-a.com/) |
| Chili's | all except Oregon | [www.chilis.com](https://www.chilis.com/) |
| Chipotle | all except Alaska and Hawaii | [www.chipotle.com](https://www.chipotle.com/) |
| Dairy Queen | all except Vermont | [www.dairyqueen.com](https://www.dairyqueen.com/) |
| Domino's | all 50 | [www.dominos.com](https://www.dominos.com/) |
| Five Guys | all 50 | [www.fiveguys.com](https://www.fiveguys.com/) |
| IHOP | all except Vermont | [www.ihop.com](https://www.ihop.com/) |
| Jersey Mike's Subs | all 50 | [www.jerseymikes.com](https://www.jerseymikes.com/) |
| Kentucky Fried Chicken | all 50 | [www.kfc.com](https://www.kfc.com/) |
| Little Caesar's | all 50 | [www.littlecaesars.com](https://www.littlecaesars.com/) |
| McDonald's | all 50 | [www.mcdonalds.com](https://www.mcdonalds.com/) |
| Olive Garden | all 50 | [www.olivegarden.com](https://www.olivegarden.com/) |
| Outback Steakhouse | all except Maine, Vermont, Rhode Island and North Dakota | [www.outback.com](https://www.outback.com/) |
| Panda Express | all except Vermont | [www.pandaexpress.com](https://www.pandaexpress.com/) |
| Panera | all except Alaska and Hawaii | [www.panerabread.com](https://www.panerabread.com/) |
| Papa John's | all except Vermont | [www.papajohns.com](https://www.papajohns.com/) |
| Pizza Hut | all 50 | [www.pizzahut.com](https://www.pizzahut.com/) |
| Popeyes | all except Vermont and Wyoming | [www.popeyes.com](https://www.popeyes.com/) |
| Sonic Drive-In | all except Maine, New Hampshire and Vermont | [www.sonicdrivein.com](https://www.sonicdrivein.com/) |
| Starbucks | all 50 | [www.starbucks.com](https://www.starbucks.com/) |
| Subway | all 50 | [www.subway.com](https://www.subway.com/) |
| Taco Bell | all 50 | [www.tacobell.com](https://www.tacobell.com/) |
| Texas Roadhouse | all except Hawaii | [www.texasroadhouse.com](https://www.texasroadhouse.com/) |
| Wendy's | all 50 | [www.wendys.com](https://www.wendys.com/) |

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

**Pictures.** For every meal without a picture, the script searches DuckDuckGo Images with the restaurant and the item **each in double quotes**, exactly as you would type it in the search box, and stores the first https image URL (`image_url`, plus the full-size `image_full_url` as a fallback). Olive Garden's Fettuccine Alfredo is searched as:

```
"Olive Garden" "Fettuccine Alfredo"
```

If that finds nothing, it tries again without any `(...)` in the item name (`"Chipotle" "Chicken Taco"` for `Chicken Taco (Soft Flour)`). A meal that still has no picture at all then gets one unquoted search (`<restaurant> <item>`; turn that off with `IMAGE_FALLBACK_TO_WEB = False`). Each picture records which search found it in `_image_source`: `quoted` or `web` (the unquoted fallback). There is no cap on the number of searches. The picture step stops early if DuckDuckGo starts failing several searches in a row (rate limiting) or after 60 minutes (`IMAGE_TIME_BUDGET_MINUTES`); the remaining meals get pictures on the next run. A meal gives up after 3 failed attempts.

**Refreshing every picture (manual).** To redo all the pictures with the quoted search, open the **Actions** tab, choose **Refresh all meal images (manual)** and click **Run workflow** (or run `python refresh_images.py` locally). For each meal it runs the search above and replaces the picture with the first result; if nothing is found, the meal keeps its current picture. DuckDuckGo throttles fast searching, so it pauses a few seconds between searches and, when a search fails, cools down (30 s up to 5 min) and retries the same meal; only after 6 failures in a row does it stop and save. A full refresh of the ~2,650+ meals takes a few runs. Each finished meal is marked `_image_refreshed` with the name of the search method (`IMAGE_STRATEGY`), so a run that stops early is simply run again and continues where it stopped, and pictures found by an older method (like the earlier `site:` search) are redone. Inputs: *restart* (redo everything), *restaurant* (just one chain, good for a quick test), *delay* (seconds between searches), *time budget*. New meals are searched the same way and marked as done, so this only needs running once for the existing list.

### Running it locally

```bash
pip install requests ddgs
GEMINI_KEY=your_key python pull_meals.py
```

Options: `--skip-images` (Gemini only), `--images-only` (pictures only), `--output path/to/meals.json`.

Refresh every picture: `python refresh_images.py` (options: `--restaurant "Olive Garden"`, `--limit 25`, `--restart`, `--delay 3`, `--time-budget-minutes 100`).

Environment variables: `GEMINI_KEY` (required for the Gemini step), `MAX_GEMINI_CALLS` (per model per day, default 20), `MAX_CALLS_PER_RUN` (default 20).

### `meals.json` shape

```json
{
  "updated_at": "...",
  "allowed_restaurants": ["Applebee's", "..."],
  "restaurants": { "Chick-fil-A": { "except_states": ["Vermont", "Alaska"], "site": "www.chick-fil-a.com", "last_pulled": "..." } },
  "quota": { "date": "2026-10-16", "calls": { "gemini-3.8-flash": 7 }, "exhausted": [] },
  "pending": ["Five Guys", "..."],
  "meals": [
    {
      "restaurant": "McDonald's", "name": "Big Mac", "category": "regular",
      "calories": 590, "price": 6.49, "serving": "1 sandwich",
      "vegetarian": false, "healthy": false,
      "image_url": "https://...", "image_full_url": "https://..."
    }
  ]
}
```

Fields that start with an underscore (`_added_at`, `_model`, `_image_attempts`, `_image_refreshed`, `_image_source`, `_serving_multiplier`) are bookkeeping. If you ever want a clean start, delete `meals.json` and run the workflow again.

## How the plan works (`index.html`)

- Enter weight, age and activity level to get a daily calorie target (or type your own). Each day lands within 250 calories of the target.
- **Meals per day:** breakfast, lunch and dinner, plus a **late night** meal when the target is high enough. Late night always comes from the **dessert** category.
  - Normal: 3 meals up to 2800 calories, 4 meals above 2800.
  - With **Healthy** checked: 3 meals up to 2300 calories, 4 meals above 2300.
- Breakfast comes from the breakfast category; lunch and dinner from the regular category.
- **Filters:** *Max per meal ($)*, *Healthy* and *Vegetarian*. If a filter would leave too few meals, the plan falls back to the unfiltered pool and says so. Desserts (late night) are only affected by the price cap and the vegetarian filter.
- **Select** opens `products.html` to hand-pick a meal for a slot; **regen** swaps in a random one.
- Each meal has icon links, squeezed together: first the **restaurant's own website** (its favicon links to the official site from the table above), then **DoorDash, Uber Eats and Grubhub**, which search for the restaurant name. All icons are the sites' favicons, loaded from Google's favicon service.
- Weeks can be saved as tabs. They live in your browser only.

## Customizing

| What | Where |
|---|---|
| Restaurants, state exclusions and official sites (`site`) | `RESTAURANTS` in `pull_meals.py` (and the fallback copy near `restaurantInfo` in `index.html`) |
| Models and per-day limits | `GEMINI_MODELS`, `MAX_GEMINI_CALLS_PER_DAY`, `MAX_GEMINI_CALLS_PER_RUN` in `pull_meals.py` |
| How many new items per Gemini call | `MAX_NEW_MEALS_PER_CALL` in `pull_meals.py` |
| Run schedule | the `cron` line in `.github/workflows/pull_meals.yml` (GitHub cron is UTC) |
| Calorie/price sanity bounds | `MIN_CALORIES`, `MAX_CALORIES`, `MIN_PRICE`, `MAX_PRICE` in `pull_meals.py` |
| 3 vs 4 meals thresholds | `getMealSlotsForTarget` in `index.html` |
| Header picture | `--hero-photo` in the CSS of both HTML files: a photo of a fast food drive-thru ordering menu ("Digital Menu Boards in the Drive-Thru" by Erik Mclean on Pexels; free to use under the Pexels license, no credit required). Swap in any image URL you have rights to use. If the photo can't load, `--hero-fallback` (an inline illustration) shows instead. |

## Notes and limits

- Gemini's menu knowledge can be wrong or out of date. Numbers are estimates, not live menu data.
- Pictures come from DuckDuckGo searches for the quoted restaurant and item, but they aren't verified, so a picture may not match the item exactly (menus change and some items have no good photo online).
- Delivery-app search links go to each app's search page; results depend on your location.
- DuckDuckGo rate-limits aggressively, so a big backlog of pictures fills in over several runs.
