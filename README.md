# Lazy Man's Fast Food Diet Plan

A static website that builds a 7-day meal plan, **only from 20 national fast food chains**, to hit a daily calorie target. Menu data (calories, price, serving, healthy/vegetarian flags) is gathered automatically by Google Gemini, and pictures come from DuckDuckGo Images. A GitHub Action refreshes it 4 times a day.

> All calories, prices and servings are **AI estimates**. Menus and prices vary by location, so double-check at the restaurant.

## Files

| File | What it does |
|---|---|
| `index.html` | The home page: enter weight/age/activity, get a 7-day plan, swap meals, save weeks, order links. |
| `products.html` | "Menu Items": a searchable table of every meal in the data, and the picker used by the home page's **select** button. |
| `pull_meals.py` | Asks Gemini for menu items, finds pictures on DuckDuckGo, and writes `meals.json`. |
| `.github/workflows/pull_meals.yml` | GitHub Action that runs `pull_meals.py` 4 times a day and commits `meals.json`. |
| `meals.json` | **Created by the first run.** All meals plus run state. Both pages read it. |

Repo layout:

```
index.html
products.html
pull_meals.py
.github/workflows/pull_meals.yml
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

The list lives in `RESTAURANTS` at the top of `pull_meals.py` and is copied into `meals.json`, which is what the page's "Where the meals come from" section shows. The pages also ignore any meal whose restaurant isn't on that list.

## How the data is pulled (`pull_meals.py`)

**Gemini, one restaurant per call.** Each call asks for up to 60 new items for one restaurant. The names already on file are sent along so Gemini only returns new ones; the list grows a little every run. Each item comes back with:

- `category`: `breakfast`, `regular` or `dessert` (desserts, slushes, shakes, malts, sweet blended drinks)
- `calories`, `price`, `serving`
- `vegetarian` and `healthy` (yes/no, set by Gemini; desserts are never healthy)

Beverages, dips, sauces and condiments are excluded, and any item with an unknown category or an out-of-range calorie/price value is dropped.

**Models and daily limits.** Gemini Flash **3.8, 3.7, 3.6, 3.5** are tried in that order. Each model has its own allowance of 20 calls per Pacific-time day (`MAX_GEMINI_CALLS`), and each run makes at most 20 calls (`MAX_CALLS_PER_RUN`), which is one pass over the restaurants. Only answers Google actually served count toward the allowance.

- A model that answers with a 4xx (out of quota, unknown model ID) is skipped for the rest of that day.
- A model that returns 503/errors makes that restaurant fall through to the next model. After 3 failures in a row the model is skipped for the rest of the run and tried again on the next run.

**Resuming.** `meals.json` keeps a `pending` list of restaurants not yet pulled in the current pass. A restaurant leaves the list only after a successful pull, so the next run starts exactly where the last one stopped. When the list empties, a new pass begins.

**Pictures.** For every meal without a picture, the script searches DuckDuckGo Images for `"<restaurant> <item>"` and stores the image URL (`image_url`, plus the full-size `image_full_url` as a fallback). There is no cap on the number of searches. The picture step stops early if DuckDuckGo starts failing several searches in a row (rate limiting) or after 60 minutes (`IMAGE_TIME_BUDGET_MINUTES`); the remaining meals get pictures on the next run. A meal gives up after 3 failed attempts.

### Running it locally

```bash
pip install requests ddgs
GEMINI_KEY=your_key python pull_meals.py
```

Options: `--skip-images` (Gemini only), `--images-only` (pictures only), `--output path/to/meals.json`.

Environment variables: `GEMINI_KEY` (required for the Gemini step), `MAX_GEMINI_CALLS` (per model per day, default 20), `MAX_CALLS_PER_RUN` (default 20).

### `meals.json` shape

```json
{
  "updated_at": "...",
  "allowed_restaurants": ["Applebee's", "..."],
  "restaurants": { "Chick-fil-A": { "except_states": ["Vermont", "Alaska"], "last_pulled": "..." } },
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

Fields that start with an underscore (`_added_at`, `_model`, `_image_attempts`) are bookkeeping. If you ever want a clean start, delete `meals.json` and run the workflow again.

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
| Restaurants and state exclusions | `RESTAURANTS` in `pull_meals.py` (and the fallback copy near `restaurantInfo` in `index.html`) |
| Models and per-day limits | `GEMINI_MODELS`, `MAX_GEMINI_CALLS_PER_DAY`, `MAX_GEMINI_CALLS_PER_RUN` in `pull_meals.py` |
| How many new items per Gemini call | `MAX_NEW_MEALS_PER_CALL` in `pull_meals.py` |
| Run schedule | the `cron` line in `.github/workflows/pull_meals.yml` (GitHub cron is UTC) |
| Calorie/price sanity bounds | `MIN_CALORIES`, `MAX_CALORIES`, `MIN_PRICE`, `MAX_PRICE` in `pull_meals.py` |
| 3 vs 4 meals thresholds | `getMealSlotsForTarget` in `index.html` |
| Header picture | `--hero-photo` in the CSS of both HTML files: a photo of a fast food menu from Wikimedia Commons ("Tasty Made menu", released under CC0, so no credit is required). Swap in any image URL you have rights to use. If the photo can't load, `--hero-fallback` (an inline illustration) shows instead. |

## Notes and limits

- Gemini's menu knowledge can be wrong or out of date. Numbers are estimates, not live menu data.
- DuckDuckGo image results aren't verified, so a picture may not match the item exactly.
- Delivery-app search links go to each app's search page; results depend on your location.
- DuckDuckGo rate-limits aggressively, so a big backlog of pictures fills in over several runs.
