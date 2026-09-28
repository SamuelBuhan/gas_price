# À la pompe — Python edition

A small Streamlit app that displays French fuel prices and availability from the government's open data feed, then saves observed prices in a local SQLite database. The Python lives in one file so it is easy to read and modify.

## Run locally

1. Install Python 3.10 or newer.
2. Open a terminal in this folder.
3. Install packages: `python -m pip install -r requirements.txt`
4. Start the app: `python -m streamlit run app.py`
5. Open the local address Streamlit prints, usually `http://localhost:8501`.

## Publish from GitHub

GitHub Pages only serves static sites and cannot run Python. Push this folder to a GitHub repository, then:

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/).
2. Choose **Create app**, select your repository and branch, and set the main file path to `app.py`.
3. Deploy. Streamlit installs packages from `requirements.txt`.

## How the code works

- `load_stations()` downloads a page from the public API and caches it for ten minutes.
- `make_table()` turns each API record into readable columns.
- `is_fuel_available()` checks the feed's availability and rupture fields.
- `save_prices()` records a snapshot in `fuel_history.db` (one per ten-minute window).
- `read_history()` reads saved observations for the history chart.
- Streamlit widgets collect filters; `st.map()` and `st.dataframe()` show the results.

The history grows only while the app is running and being visited; it does not backfill older prices. Keep a copy of `fuel_history.db` to preserve local history.

## Database and online deployment

SQLite stores data in a simple file called `fuel_history.db`, created beside `app.py`. This is convenient for learning and for running the app on your own computer. Streamlit Community Cloud may replace its local files during a restart or redeploy, so this SQLite file is not a durable database for a public hosted service. For permanent shared history online, connect the app to a hosted PostgreSQL database such as Supabase; that needs a database account and connection secret.

## Current limitation

The app loads 100 records as a lightweight starter, not every station in France. The city filter only searches those loaded records. A full nationwide app should request records for the chosen location, or load and index all pages of the dataset, before people rely on it for trip planning.
