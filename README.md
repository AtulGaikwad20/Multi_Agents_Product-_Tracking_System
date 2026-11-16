# RESQUORA AI Agent Playground

Type or speak what you want to buy, in any language. A team of AI agents searches the shops for you, a Supervisor
double-checks everything they find, and you get a report you can read, hear and download.

## How it works

1. You send a request, for example *"steel almirah under ₹15000"*.
2. The **Supervisor** works out what the product is and picks only the agents whose shops sell it.
3. The agents research **at the same time** (Amazon, Flipkart, Myntra, and so on).
4. The Supervisor **verifies** the results: prices, links, images and your budget.
5. You get the final report on screen and spoken aloud. You can download it as PDF, HTML, CSV, Markdown or JSON.

You can interrupt or change your request at any time ("make it under 800", "also check Croma", "which is cheapest?").
Nothing starts over.

## The golden rule: no made-up data

Every price, rating, stock status, image and link has a label:

| Label | Meaning |
|---|---|
| **Verified** | Read from the shop's own product page |
| **Reported** | Came from the search service, not confirmed on the shop's page |
| **Unavailable** | The shop says it is out of stock or does not exist |
| **Could not retrieve** | We tried and failed (blocked, error or missing) |

There is no demo mode. With no search key, the agents say they cannot research. If a shop blocks automated access,
the app **does not work around it**. Those items show as *Reported* instead.

## Setup

1. Install the requirements:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill it in. You need at least:
   - `SECRET_KEY`
   - `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
   - a search key, for example `SERPAPI_API_KEY` (without it, no research is possible)
   - `ANTHROPIC_API_KEY` (optional: better understanding, other languages, spoken summaries)
   - `USER_AGENT` (a contact URL or email, so shops can reach you)
3. Create the database tables. Create `DB_NAME` first, then run both files **in order**, against that same database:
   ```bash
   mysql --host=YOUR_DB_HOST --port=3306 --user=YOUR_DB_USER --password YOUR_DB_NAME < migrations/000_base_tables.sql
   mysql --host=YOUR_DB_HOST --port=3306 --user=YOUR_DB_USER --password YOUR_DB_NAME < migrations/001_research_runs.sql
   ```
   On Windows you can also open and run each file in MySQL Workbench. Existing tables are never changed or deleted.
4. Check that everything is ready, then start the app:
   ```bash
   python check_setup.py --live
   flask --app run run
   ```
5. Open the app, sign in, and you land in the Command Center (`/playground`).

Never commit your passwords or keys. Keep them only in `.env`.

## The screens

- **Login / Register** take you straight to the Command Center.
- **Command Center:** the current request, live counters, a progress bar, the agents at work, the meeting room and the
  task board.
- **Right-hand tabs:**
  - **Report:** comparison, recommendations and downloads
  - **Evidence:** every fact with its label, source and time
  - **Log:** everything the team did

## Voice

The **Speak** button turns on your microphone. The gear next to it lets you test the microphone, turn auto-send off,
or turn on hands-free mode. Saying "stop" interrupts the team. Voice needs Chrome, Edge or Safari and an internet
connection.

## Location (optional)

The app only asks if your request needs it (for example "near me"), and only when you agree. You can type a city or
postal code instead, or skip it. Your exact position is thrown away after the place name is found, and the place name
is kept for 24 hours. Anything based on location is shown as an **estimate**, never as confirmed by a shop.

## Your search history

Each search and its full report is saved to your account in the `research_runs` table, so you can reopen it on any
device. History is private to you. If the database is not reachable, the app stops with a clear message and does not
silently fall back to memory. `PLAYGROUND_STORE=memory` is for development and tests only.

Your old `products` table is left untouched but is no longer needed. Do not delete `search_history` until you have
checked that its contents are backed up.

## Tests

```bash
python -m pytest -q
```

182 tests pass (5 database tests are skipped unless a test MySQL database is set up). The tests use fake network
responses. The app itself contains no fake data.

## Good to know

- Results depend on what each shop and search service allows, so coverage varies by shop. The **Log** and **Evidence**
  tabs show exactly what each agent could and could not do.
- Which shop sells what is set in `app/playground/capabilities.py`. Edit it as shops change.
- Shops that don't publish reviews give us no review text, and the report says so.
- Stock for fast-delivery shops like Blinkit can't be confirmed without the shop's own data.
- The app runs as a single process. Running several copies would need Redis (not built yet).
- Install a Unicode font (for example `fonts-dejavu-core`) on the server so PDFs show the ₹ sign.

## Where things are

- Code: `app/playground/`
- Screens: `app/templates/playground.html`, `app/static/js/pg/`, `app/static/css/playground.css`
- Database scripts: `migrations/`
- Plan and progress: `docs/AGILE_PLAN.md`
