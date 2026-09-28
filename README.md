# À la pompe — French fuel price dashboard

A beginner-friendly Python and Streamlit dashboard for finding fuel stations in France. It shows current prices and reported availability on an interactive map and in a table, and keeps a local history of observed prices.

## Features

- Browse six fuel types: Gazole, SP95, SP98, E10, E85, and GPL.
- View stations on an interactive map. Hover over a marker to see the station name, address, price, and availability.
- Compare station details and prices in a table.
- Search around a city or postal code, or allow your browser to share your current location and filter by distance.
- Use the “Center search on my position” button to return the search to your current location.
- Filter to stations reporting that the selected fuel is available and limit the number of displayed stations.
- View saved price observations in a history chart. The station selector includes its name, address, and ID to distinguish stations with similar names.

Location is requested by the browser. The app keeps the coordinates in the current Streamlit session only; it does not save them in the database.

## Run locally

1. Install Python 3.10 or newer.
2. Open a terminal in this folder.
3. Install the dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```
4. Start the app:

   ```bash
   python -m streamlit run app.py
   ```
5. Open the local address printed by Streamlit, usually [http://localhost:8501](http://localhost:8501).

## Data sources

- Live prices, station addresses, coordinates, and reported fuel availability come from the French government’s [fuel price dataset](https://www.data.gouv.fr/datasets/prix-des-carburants-en-france-flux-instantane-v2-amelioree).
- Station brand names are supplemented from the [Chiffrex / OpenStreetMap station name reference](https://www.data.gouv.fr/datasets/referentiel-des-noms-et-enseignes-de-stations-service-enrichi-par-openstreetmap) when an official station ID matches.
- City and postal code search uses the IGN Géoplateforme geocoder.

The live station feed is cached for ten minutes. The app requests the feed in pages and loads the available records before applying search filters. Prices and availability are those reported by stations and may change between updates.

## Price history and storage

The app creates `fuel_history.db` beside `app.py` and uses SQLite to store station details and observed prices. A station’s price is saved at most once per ten-minute interval when the app loads the live data. The history chart becomes useful after multiple observations; it does not contain prices from before the app began recording them.

This SQLite file works for local use. On Streamlit Community Cloud, the app’s local filesystem is not guaranteed to persist across restarts or redeployments, so the hosted history can be lost. For dependable shared history in a public deployment, the app needs to be connected to a hosted database such as Supabase. That integration is not included yet. Back up `fuel_history.db` if you want to preserve local history.

## Main files

- `app.py` — the Streamlit app, data loading, map, filters, and SQLite history.
- `requirements.txt` — Python packages installed locally and during deployment.
