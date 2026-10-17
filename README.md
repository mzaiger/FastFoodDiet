# Lazy Man's Fast Food Diet

A static website that builds a 7-day meal plan, **only from 20 national fast food chains**, to hit a daily calorie target. Menu data (calories, price, serving, healthy/vegetarian flags) is gathered automatically by Google Gemini, and pictures come from DuckDuckGo Images. A GitHub Action refreshes it 4 times a day.

> All calories, prices and servings are **AI estimates**. Menus and prices vary by location, so double-check at the restaurant.

## Files

| File | What it does |
|---|---|
| `index.html` | The home page: enter weight/age/activity, get a 7-day plan, swap meals, save weeks, order links. |
| `products.html` | "Menu Items": a searchable table of every meal in the data, and the picker used by the home page's **select** button. |
| `pull_meals.py` | Asks Gemini for menu items, finds pictures on DuckDuckGo, and writes `meals.json`. |
| `.github/workflows/pull_meals.yml` | GitHub Action that runs `pull_meals.py` 4 times a day and commits `meals.json`. |
| `refresh_images.py` | Manual tool: redoes **every** meal picture by searching each restaurant's own website (see "Pictures"). |
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

Meals can only come from these 20 chains. Each has locations in all 50 states except where noted:

Applebee's, Burger King, Chick-fil-A (not VT, AK), Chipotle (not AK, HI), Dairy Queen (not VT), Domino's, Five Guys, IHOP (not VT), Jersey Mike's Subs, Kentucky Fried Chicken, Little Caesar's, McDonald's, Olive Garden, Panera (not AK, HI), Pizza Hut, Starbucks, Subway, Taco Bell, Texas Roadhouse (not HI), Wendy's.

Official websites, used for the picture search (`site:` searches, see "Pictures"):

| Restaurant | Site | Restaurant | Site |
|---|---|---|---|
| Applebee's | `www.applebees.com` | Pizza Hut | `www.pizzahut.com` |
| Burger King | `www.bk.com` | Starbucks | `www.starbucks.com` |
| Chick-fil-A | `www.chick-fil-a.com` | Subway | `www.subway.com` |
| Chipotle | `www.chipotle.com` | Taco Bell | `www.tacobell.com` |
| Dairy Queen | `www.dairyqueen.com` | Texas Roadhouse | `www.texasroadhouse.com` |
| Domino's | `www.dominos.com` | Wendy's | `www.wendys.com` |
| Five Guys | `www.fiveguys.com` | Kentucky Fried Chicken | `www.kfc.com` |
| IHOP | `www.ihop.com` | Little Caesar's | `www.littlecaesars.com` |
| Jersey Mike's Subs | `www.jerseymikes.com` | McDonald's | `www.mcdonalds.com` |
| Olive Garden | `www.olivegarden.com` | Panera | `www.panerabread.com` |

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

**Models and daily limits.** Gemini Flash **3.8, 3.7, 3.6, 3.5** are tried in that order. Each model has its own allowance of 20 calls per Pacific-time day (`MAX_GEMINI_CALLS`), and each run makes at most 20 calls (`MAX_CALLS_PER_RUN`), which is one pass over the restaurants. Only answers Google actually served count toward the allowance.

- A model that answers with a 4xx (out of quota, unknown model ID) is skipped for the rest of that day.
- A model that returns 503/errors makes that restaurant fall through to the next model. After 3 failures in a row the model is skipped for the rest of the run and tried again on the next run.

**Resuming.** `meals.json` keeps a `pending` list of restaurants not yet pulled in the current pass. A restaurant leaves the list only after a successful pull, so the next run starts exactly where the last one stopped. When the list empties, a new pass begins.

**Pictures.** For every meal without a picture, the script searches DuckDuckGo Images **on the restaurant's own website** and stores the image URL (`image_url`, plus the full-size `image_full_url` as a fallback). The query is the site plus the item name, for example Olive Garden's Fettuccine Alfredo is searched as:

```
site:www.olivegarden.com Fettuccine Alfredo
```

If the official site has nothing for a meal that has no picture at all, the script falls back to an open search for `"<restaurant> <item>"` (turn that off with `IMAGE_FALLBACK_TO_WEB = False`). Each picture records where it came from in `_image_source`: `site` (official website) or `web` (fallback). There is no cap on the number of searches. The picture step stops early if DuckDuckGo starts failing several searches in a row (rate limiting) or after 60 minutes (`IMAGE_TIME_BUDGET_MINUTES`); the remaining meals get pictures on the next run. A meal gives up after 3 failed attempts.

**Refreshing every picture (manual).** Pictures found before this strategy came from open searches. To redo all of them from the official sites, open the **Actions** tab, choose **Refresh all meal images (manual)** and click **Run workflow** (or run `python refresh_images.py` locally). For each meal it searches `site:<restaurant site> <item>` and replaces the picture with the first result; if the official site has nothing, the meal keeps its current picture. It marks each finished meal with `_image_site_checked`, so a run that stops early (rate limiting, time budget) is simply run again and continues where it stopped. Inputs: *restart* (redo everything), *restaurant* (just one chain, good for a quick test), *time budget*. New meals are searched the same way and marked as done, so this only needs running once for the existing list.

### Running it locally

```bash
pip install requests ddgs
GEMINI_KEY=your_key python pull_meals.py
```

Options: `--skip-images` (Gemini only), `--images-only` (pictures only), `--output path/to/meals.json`.

Refresh every picture from the official sites: `python refresh_images.py` (options: `--restaurant "Olive Garden"`, `--limit 25`, `--restart`, `--time-budget-minutes 100`).

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

Fields that start with an underscore (`_added_at`, `_model`, `_image_attempts`, `_image_site_checked`, `_image_source`, `_serving_multiplier`) are bookkeeping. If you ever want a clean start, delete `meals.json` and run the workflow again.

## How the plan works (`index.html`)

- Enter weight, age and activity level to get a daily calorie target (or type your own). Each day lands within 250 calories of the target.
- **Meals per day:** breakfast, lunch and dinner, plus a **late night** meal when the target is high enough. Late night always comes from the **dessert** category.
  - Normal: 3 meals up to 2800 calories, 4 meals above 2800.
  - With **Healthy** checked: 3 meals up to 2300 calories, 4 meals above 2300.
- Breakfast comes from the breakfast category; lunch and dinner from the regular category.
- **Filters:** *Max per meal ($)*, *Healthy* and *Vegetarian*. If a filter would leave too few meals, the plan falls back to the unfiltered pool and says so. Desserts (late night) are only affected by the price cap and the vegetarian filter.
- **Select** opens `products.html` to hand-pick a meal for a slot; **regen** swaps in a random one.
- Each meal has icon links that search **DoorDash, Uber Eats and Grubhub** for the restaurant name. The icons are the sites' favicons, loaded from Google's favicon service.
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
- Pictures come from DuckDuckGo searches limited to each restaurant's website, but they aren't verified, so a picture may still not match the item exactly (menus change and some items have no page). If a chain's pictures look wrong, check its `site` in `RESTAURANTS`.
- Delivery-app search links go to each app's search page; results depend on your location.
- DuckDuckGo rate-limits aggressively, so a big backlog of pictures fills in over several runs.
