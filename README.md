# RESQUORA AI Agent Playground

A team of AI research agents that work like office employees. You speak or type a request (any language, any
product). A **Supervisor** classifies it, holds a kick-off meeting, assigns only the agents whose platforms really
sell that kind of product, lets them research in parallel, holds a findings meeting, **verifies everything**, and
then reports aloud and on screen, with evidence labels, links, images, timestamps and downloadable reports.

## The principle: evidence, not performance
Every price, rating, stock status, image and link carries a status:

| Status | Meaning |
|---|---|
| **Verified** | Read from the retailer's own product page at the time shown |
| **Reported** | Supplied by the licensed search API; *not* confirmed at source |
| **Unavailable** | The retailer says it is out of stock / doesn't exist |
| **Could not retrieve** | We tried and couldn't (blocked, error, absent) |

There is no demo or sample mode. With no search key the agents say they cannot research. Sites that forbid automated
access (robots.txt, bot walls) are **not bypassed**; their items appear as *Reported* with the reason.

## Architecture
```
Browser / PWA   Flask application (/api/...)   Services
 voice / text ──────────────────────────────────────► POST /api/runs ─────► Supervisor ──► Agents
 live SSE ◄───────────────────────────────────────── GET /events             │ meetings       ├ Search providers
 reports · evidence · history · exports                 │ Verifier       ├ SafeFetcher (robots, SSRF, throttle)
                                                       └ MySQL research_runs └ JSON-LD extractor
```
Code: `app/playground/` — `capabilities.py` (who covers what), `agents.py`, `net.py`, `extract.py`, `search.py`,
`understanding.py`, `supervisor.py`, `verification.py`, `reports.py`, `location.py`, `store.py`, `routes.py`.
UI: `templates/playground.html`, `static/js/pg/*`, `static/css/playground.css`, `static/pwa/*`.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env            # fill SECRET_KEY, DB_*, SERPAPI_API_KEY, ANTHROPIC_API_KEY, USER_AGENT
mysql --host=YOUR_DB_HOST --port=3306 --user=YOUR_DB_USER --password YOUR_DB_NAME < migrations/000_base_tables.sql
mysql --host=YOUR_DB_HOST --port=3306 --user=YOUR_DB_USER --password YOUR_DB_NAME < migrations/001_research_runs.sql
python check_setup.py --live
flask --app run run            # or gunicorn -k gthread --threads 16 -w 1 run:app
```
Replace the `YOUR_DB_*` placeholders with the non-secret connection values from `.env`; MySQL prompts for the
password. On Windows, the same migration can be run from MySQL Workbench by opening and executing the SQL file.
Set `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` and `DB_NAME` in the environment (or `.env`); never commit
database credentials. The MySQL user needs access to the selected database and read/write privileges on
`users` and `research_runs`. The app checks its connection and required tables at startup and will fail clearly if
either is unavailable; it never silently switches to memory storage. Each search is saved before its research worker starts,
then its event history, findings and report are saved when processing finishes. History is private to the signed-in
user. `PLAYGROUND_STORE=memory` is intended only for development/tests and does not survive a restart.
Search history and complete reports are stored in `research_runs`. The app no longer writes report product details to
a separate `products` catalog because those details already exist in each report. Any existing `products` table and its
data are left untouched, but are no longer required. `search_history` is not used by this app; preserve it unless you
have verified its contents are backed up or migrated. Run `python check_setup.py` to see which database is selected
and how many history records it contains. The setup check
can inspect an incomplete schema without the app's normal startup check blocking it.

Create `DB_NAME` first and run both migrations in order. `000_base_tables.sql` creates the authentication table only
when missing; `001_research_runs.sql` adds the per-user history table. Existing tables (including `products`) are not
altered or dropped. Do not run migrations against a different database than the one configured with `DB_NAME`.
Single worker process: run state and event streams live in memory (SSE). Scaling out needs Redis pub/sub (Sprint 6).
Install a Unicode font (e.g. `fonts-dejavu-core`) on the server so PDFs show ₹.

## Screens
* **Login / Register** go straight into the **Multi-Agent Command Center** (`/playground`). The old dashboard was
  removed: its search box, "recent searches", "tracked products" and five sidebar links were either dead or duplicated
  by the playground. `/dashboard` redirects to the playground so old bookmarks keep working.
* **Command Center:** mission strip (current request, live agent / search / page / listing counters, timer), a
  six-stage progress stepper, the agent floor, the meeting room and the task board.
* **Right-hand tabs:** **Report** (comparison, recommendations, exports), **Evidence** (the source-verification panel:
  every fact with its status, source site, retrieval time and note; conflicts and cautions; what each agent actually
  did; exclusions; assumptions) and **Log**.

## Microphone
The Speak button has a settings gear: permission status, a real microphone test with a level meter, auto-send on/off,
and hands-free mode (listens again after the Supervisor finishes speaking). Saying "stop" or "cancel" interrupts the
team and is never treated as a search. Spoken replies are read sentence by sentence so long replies are not cut off.
Errors are explained in plain words (blocked permission, no device, offline, unsupported browser or language). Speech
recognition uses the browser's own service, so it needs Chrome, Edge or Safari and an internet connection; Firefox and
Brave do not support it.

## Using it
Type or tap the mic. Examples: *"steel almirah under ₹15000"*, *"boAt earbuds under 2k"*,
*"मुझे 500 रुपये से कम में सफेद कुर्ता चाहिए"*. While it works you can interrupt and refine ("make it under 800",
"also check Croma", "which is cheapest?") without starting over.
When an AI model is configured, the Supervisor can interpret obvious spelling, grammar, and speech-to-text
mistakes while preserving the product details and limits you gave. Without an AI model, it falls back to basic
keyword and price parsing, which may not understand typo-heavy requests.

## Location (optional)
Asked only via an explicit dialog. One-shot. Coordinates are discarded after the place name is resolved; only the label
is kept for 24 h; "Forget my location" deletes it. Denying is fine: type a city/postal code or skip. Used only if the
request needs it, and anything derived from it is labelled an estimate, never "retailer-confirmed".

## Tests
`python -m pytest -q` → 182 pass, 5 DB tests skip unless a `*test*` MySQL database is configured. Tests use doubles for
the network; the app ships no fake data. CI: `.github/workflows/ci.yml`.

## Defects the end-to-end testing caught (and fixed)
Internet/Shopify agents crashed on domain-less profiles · the findings meeting claimed "60% below" with the maths
inverted and compared different products · the summary called an out-of-stock item "unverified" · reports were saved
before their last events (another device would never show the report) · min-max price scaling let an unconfirmed
listing beat a verified one over a 2.5% price gap · brand "boAt" was stripped as a platform name.

## Honest limits
* **Not run against live sites or live APIs** (no keys/network here). Selector-free by design (schema.org JSON-LD), but
  real-world coverage per retailer must be validated (see `docs/AGILE_PLAN.md` §3).
* Coverage profiles in `capabilities.py` are my best knowledge of what each platform sells; they are a prior, and agents
  still need real evidence. Edit them as platforms change.
* MySQL store/migration written but untested here; Hyperlocal stock (Blinkit) cannot be confirmed without retailer data.
* Review "summaries" use only review text published on the page; many sites publish none, and the report says so.#   M u l t i _ A g e n t s _ P r o d u c t - _ T r a c k i n g _ S y s t e m  
 