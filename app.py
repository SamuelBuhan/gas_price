"""À la pompe — a beginner-friendly French fuel price dashboard.

Run locally with:  python -m streamlit run app.py
The app reads the French government's public fuel dataset.
"""

import html
import io
import math
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
import pydeck as pdk
import streamlit as st
from streamlit_js_eval import get_geolocation

# Public API for the French Ministry of the Economy fuel dataset.
API_URL = (
    "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "prix-des-carburants-en-france-flux-instantane-v2/records"
)
GEOCODING_URL = "https://data.geopf.fr/geocodage/search"
STATION_NAMES_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/"
    "0207ded0-2d19-47af-b1a6-62915ec1b721"
)
DATABASE_FILE = Path(__file__).parent / "fuel_history.db"

# API field names: (price, last update, official fuel name).
FUELS = {
    "Gazole": ("gazole_prix", "gazole_maj", "Gazole"),
    "SP95": ("sp95_prix", "sp95_maj", "SP95"),
    "SP98": ("sp98_prix", "sp98_maj", "SP98"),
    "E10": ("e10_prix", "e10_maj", "E10"),
    "E85": ("e85_prix", "e85_maj", "E85"),
    "GPL": ("gplc_prix", "gplc_maj", "GPLc"),
}



def create_database():
    """Create a station directory and price history table if needed."""
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS stations (
                station_id TEXT PRIMARY KEY,
                station_name TEXT NOT NULL,
                address TEXT,
                postal_code TEXT,
                city TEXT,
                latitude REAL,
                longitude REAL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                station_id TEXT NOT NULL,
                station_name TEXT,
                fuel TEXT NOT NULL,
                price REAL NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(station_id, fuel, recorded_at)
            )
        """)


def save_prices(records, station_names):
    """Save the downloaded prices once per ten-minute time window."""
    # Round down so a rerun doesn't create duplicate history points.
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    recorded_at = now.replace(minute=(now.minute // 10) * 10).isoformat()
    with sqlite3.connect(DATABASE_FILE) as connection:
        for station in records:
            station_id = str(station.get("id", ""))
            if not station_id:
                continue
            address = station.get("adresse") or ""
            postal_code = station.get("cp") or station.get("code_postal") or ""
            city = station.get("ville") or ""
            # The price feed often omits a chain name; an address is more
            # useful than repeating the city for every station.
            station_name = (
                station_names.get(station_id)
                or station.get("enseigne")
                or station.get("nom")
                or address
                or f"Station {station_id}"
            )
            point = station.get("geom") or {}
            connection.execute(
                "INSERT INTO stations "
                "(station_id, station_name, address, postal_code, city, latitude, longitude) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(station_id) DO UPDATE SET "
                "station_name=excluded.station_name, address=excluded.address, "
                "postal_code=excluded.postal_code, city=excluded.city, "
                "latitude=excluded.latitude, longitude=excluded.longitude",
                (station_id, station_name, address, postal_code, city,
                 point.get("lat"), point.get("lon")),
            )
            # Refresh labels on history rows saved before the station table existed.
            connection.execute(
                "UPDATE price_history SET station_name = ? WHERE station_id = ?",
                (station_name, station_id),
            )
            for fuel, (price_field, _, _) in FUELS.items():
                price = station.get(price_field)
                if price is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO price_history "
                        "(station_id, station_name, fuel, price, recorded_at) VALUES (?, ?, ?, ?, ?)",
                        (station_id, station_name, fuel, float(price), recorded_at),
                    )


def read_history(station_id, fuel):
    """Read saved prices for one station and fuel as a chart-ready table."""
    with sqlite3.connect(DATABASE_FILE) as connection:
        return pd.read_sql_query(
            "SELECT recorded_at AS Date, price AS Price "
            "FROM price_history WHERE station_id = ? AND fuel = ? ORDER BY recorded_at",
            connection,
            params=(str(station_id), fuel),
        )



@st.cache_data(ttl=600)
def load_stations():
    page_size = 100
    all_stations = []
    offset = 0
    total_count = None

    while total_count is None or offset < total_count:
        response = requests.get(
            API_URL,
            params={"limit": page_size, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        page = response.json()
        results = page.get("results", [])

        total_count = page.get("total_count", total_count)
        all_stations.extend(results)

        if not results or len(results) < page_size:
            break

        offset += len(results)

    return all_stations


def clean_column_name(value):
    """Normalize a CSV heading so accents and punctuation do not matter."""
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


@st.cache_data(ttl=86400)
def load_station_names():
    """Load the weekly OSM-enriched official-ID-to-brand reference."""
    try:
        response = requests.get(STATION_NAMES_URL, timeout=30)
        response.raise_for_status()
        reference = pd.read_csv(
            io.BytesIO(response.content), sep=None, engine="python", dtype=str
        )
        columns = {clean_column_name(column): column for column in reference.columns}

        # Tolerate reasonable changes to the dataset's column headings.
        id_column = next((columns[name] for name in (
            "id_station_officiel", "id", "id_station", "station_id", "id_station_service",
            "identifiant", "identifiant_station", "id_point_de_vente",
        ) if name in columns), None)
        name_column = next((columns[name] for name in (
            "nom_normalise", "enseigne", "nom_enseigne", "brand", "name", "nom",
            "station_name", "nom_station", "network",
        ) if name in columns), None)
        if id_column is None or name_column is None:
            return {}

        reference = reference[[id_column, name_column]].dropna()
        return {
            str(station_id).strip(): str(name).strip()
            for station_id, name in reference.itertuples(index=False, name=None)
            if str(station_id).strip() and str(name).strip()
        }
    except (requests.RequestException, pd.errors.ParserError, UnicodeDecodeError):
        return {}


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_place(place):
    """Find a place's map coordinates using the IGN Géoplateforme geocoder."""
    try:
        response = requests.get(
            GEOCODING_URL,
            params={"q": place, "limit": 1},
            timeout=15,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return None
        longitude, latitude = features[0]["geometry"]["coordinates"][:2]
        return float(latitude), float(longitude)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def recenter_on_user():
    """Clear the place search and enable the GPS-centered radius filter."""
    st.session_state["search_place"] = ""
    st.session_state["place_query_input"] = ""
    st.session_state["only_nearby"] = True


def is_fuel_available(station, official_fuel_name, fuel_key):
    """Use the API's lists and rupture field to determine availability."""
    available = station.get("carburants_disponibles") or []
    unavailable = station.get("carburants_indisponibles") or []
    if official_fuel_name in available:
        return True
    if official_fuel_name in unavailable:
        return False
    # Some records omit the lists, so fall back to the fuel-specific rupture.
    return not station.get(f"{fuel_key}_rupture_type")


def distance_km(latitude_1, longitude_1, latitude_2, longitude_2):
    """Calculate the approximate distance between two points on Earth."""
    to_radians = math.radians
    lat_1, lat_2 = to_radians(latitude_1), to_radians(latitude_2)
    lat_diff = to_radians(latitude_2 - latitude_1)
    lon_diff = to_radians(longitude_2 - longitude_1)
    arc = (
        math.sin(lat_diff / 2) ** 2
        + math.cos(lat_1) * math.cos(lat_2) * math.sin(lon_diff / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(arc), math.sqrt(1 - arc))


def make_table(records, fuel_label, station_names):
    """Turn the API's records into simple rows for the map and table."""
    price_field, update_field, official_name = FUELS[fuel_label]
    fuel_key = {"GPL": "gplc"}.get(fuel_label.lower(), fuel_label.lower())
    rows = []
    for station in records:
        # geom contains coordinates as decimal latitude and longitude.
        point = station.get("geom") or {}
        if not point.get("lat") or not point.get("lon"):
            continue
        address_parts = [station.get("adresse"), station.get("cp"), station.get("ville")]
        rows.append({
            "ID": station.get("id"),
            "Station": (
                station_names.get(str(station.get("id", "")))
                or station.get("enseigne")
                or station.get("nom")
                or station.get("adresse")
                or f"Station {station.get('id', '')}"
            ),
            "Adresse": ", ".join(str(part) for part in address_parts if part),
            "Prix (€/L)": station.get(price_field),
            "Disponibilité": "Disponible" if is_fuel_available(station, official_name, fuel_key) else "Rupture signalée",
            "Mise à jour": station.get(update_field),
            "latitude": point["lat"],
            "longitude": point["lon"],
        })
    return pd.DataFrame(rows)

if __name__ == "__main__":
    st.set_page_config(page_title="À la pompe", page_icon="⛽", layout="wide")
    st.title("⛽ À la pompe")
    st.caption("Comparez les prix et repérez les stations-service en France.")

    create_database()

    filters, results = st.columns([1, 3])
    with filters:
        chosen_fuel = st.selectbox("Carburant", list(FUELS))
        st.markdown("**Votre position**")
        st.caption("À l'ouverture, votre navigateur demande si vous autorisez l'accès à votre position.")
        location = get_geolocation(component_key="user_location_on_load")
        if isinstance(location, dict) and isinstance(location.get("coords"), dict):
            coordinates = location["coords"]
            if coordinates.get("latitude") is not None and coordinates.get("longitude") is not None:
                st.session_state["user_location"] = (
                    float(coordinates["latitude"]), float(coordinates["longitude"])
                )
        user_location = st.session_state.get("user_location")
        if user_location:
            st.success("Position reçue. Les coordonnées ne sont pas enregistrées.")
            only_nearby = st.checkbox(
                "Limiter aux stations proches", value=True, key="only_nearby"
            )
            st.button(
                "Centrer la recherche sur ma position",
                key="center_search_on_user",
                on_click=recenter_on_user,
                use_container_width=True,
            )
        else:
            if isinstance(location, dict) and location.get("error", {}).get("code") == 1:
                st.info("Accès à la position refusé. Vous pouvez chercher par ville ou code postal.")
            else:
                st.info("En attente de l'autorisation de localisation du navigateur.")
            only_nearby = False

        radius_km = st.slider("Rayon de recherche (km)", 5, 100, 25, step=5)
        with st.form("place_search_form"):
            place_query = st.text_input(
                "Ville ou code postal", placeholder="Ex. Paris ou 75001",
                key="place_query_input",
            )
            search_submitted = st.form_submit_button(
                "Rechercher les stations autour du lieu", use_container_width=True
            )
        if search_submitted:
            st.session_state["search_place"] = place_query.strip()
        search_place = st.session_state.get("search_place", "")
        place_center = geocode_place(search_place) if search_place else None
        if search_place and place_center:
            st.info(f"Recherche centrée sur {search_place} · rayon de {radius_km} km")
        elif search_place:
            st.warning("Lieu introuvable par géocodage. Recherche par code postal ou nom de commune.")

        only_available = st.checkbox("Stations approvisionnées seulement", value=True)
        max_rows = st.slider("Nombre de stations", 10, 100, 50, step=10)
        if st.button("Actualiser les données"):
            load_stations.clear()

    try:
        
        raw_stations = load_stations()
    except requests.RequestException as error:
        st.error("Les données publiques sont momentanément indisponibles. Réessayez plus tard.")
        st.caption(f"Détail technique : {error}")
        st.stop()

    station_names = load_station_names()
    save_prices(raw_stations, station_names)

    stations = make_table(raw_stations, chosen_fuel, station_names)
    # A typed place takes priority over GPS; otherwise use the user's position.
    search_center = place_center
    if search_center is None and user_location and only_nearby and not search_place:
        search_center = user_location

    if search_center is not None:
        stations["Distance (km)"] = stations.apply(
            lambda row: distance_km(
                search_center[0], search_center[1], row["latitude"], row["longitude"]
            ),
            axis=1,
        )
        stations = stations[stations["Distance (km)"] <= radius_km]
    elif search_place:
        # If geocoding is unavailable, fall back to the locality/postcode fields,
        # not arbitrary street names such as "Route de Paris".
        query = search_place.strip()
        address_parts = stations["Adresse"].fillna("").str.split(",", n=2, expand=True)
        while address_parts.shape[1] < 3:
            address_parts[address_parts.shape[1]] = ""
        if query.isdigit():
            search_values = address_parts[1].fillna("").str.strip()
        else:
            search_values = address_parts[2].fillna("").str.strip()
        stations = stations[search_values.str.contains(query, case=False, na=False)]
    if only_available:
        stations = stations[stations["Disponibilité"] == "Disponible"]
    stations = stations.sort_values("Prix (€/L)", na_position="last").head(max_rows)

    with results:
        st.subheader(f"Stations — {chosen_fuel}")
        st.caption(f"{len(stations)} résultats · source officielle actualisée toutes les 10 minutes")
        if stations.empty:
            st.info("Aucune station ne correspond aux filtres dans les données chargées.")
        else:
            # Keep tooltip columns simple and escape public text before putting it in HTML.
            map_data = stations.copy()
            map_data["hover_name"] = map_data["Station"].fillna("").map(html.escape)
            map_data["hover_address"] = map_data["Adresse"].fillna("").map(html.escape)
            map_data["hover_price"] = map_data["Prix (€/L)"].apply(
                lambda price: f"{price:.3f} €/L" if pd.notna(price) else "Prix indisponible"
            )
            map_data["hover_status"] = map_data["Disponibilité"].map(html.escape)
            map_data["marker_color"] = map_data["Disponibilité"].apply(
                lambda status: [100, 168, 74, 220] if status == "Disponible" else [222, 103, 68, 230]
            )

            # Focus the map on the user's area when available; otherwise show the results.
            if search_center is not None:
                center_latitude, center_longitude = search_center
                zoom_level = 10 if radius_km <= 25 else 8
            else:
                center_latitude = float(stations["latitude"].mean())
                center_longitude = float(stations["longitude"].mean())
                zoom_level = 5.5 if not search_place else 8

            markers = pdk.Layer(
                "ScatterplotLayer",
                data=map_data,
                get_position="[longitude, latitude]",
                get_radius=700,
                radius_min_pixels=5,
                radius_max_pixels=12,
                get_fill_color="marker_color",
                get_line_color=[255, 255, 255, 220],
                line_width_min_pixels=1,
                stroked=True,
                pickable=True,
                auto_highlight=True,
            )
            map_deck = pdk.Deck(
                layers=[markers],
                initial_view_state=pdk.ViewState(
                    latitude=center_latitude,
                    longitude=center_longitude,
                    zoom=zoom_level,
                    pitch=0,
                ),
                tooltip={
                    "html": "<b>{hover_name}</b><br/>{hover_address}<br/>{hover_price}<br/>{hover_status}",
                    "style": {
                        "backgroundColor": "#202923",
                        "color": "white",
                        "fontSize": "12px",
                        "padding": "10px",
                    },
                },
                map_style=None,
            )
            st.pydeck_chart(map_deck, use_container_width=True)
            st.dataframe(
                stations[[
                    column for column in (
                        "Station", "Adresse", "Prix (€/L)", "Disponibilité",
                        "Mise à jour", "Distance (km)",
                    ) if column in stations.columns
                ]],
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("Historique des prix")
    st.caption("Les prix sont enregistrés dans SQLite au fil des consultations, au maximum une fois toutes les 10 minutes.")
    if not stations.empty:
        selected_id = st.selectbox(
            "Choisir une station pour voir son historique",
            stations["ID"].astype(str).tolist(),
            format_func=lambda station_id: (
                lambda row: f"{row['Station']} — {row['Adresse']} (ID: {station_id})"
            )(stations.loc[stations["ID"].astype(str) == station_id].iloc[0]),
        )
        history = read_history(selected_id, chosen_fuel)
        if len(history) > 1:
            history["Date"] = pd.to_datetime(history["Date"])
            st.line_chart(history.set_index("Date")["Price"], y_label="Prix (€/L)")
        elif len(history) == 1:
            st.info("Un prix est enregistré. L'historique apparaîtra après d'autres relevés.")
        else:
            st.info("Aucun prix enregistré pour cette station et ce carburant.")
    else:
        st.info("Choisissez des filtres qui affichent au moins une station pour consulter son historique.")

    st.divider()
    st.caption(
        "Prix et ruptures déclarés par les stations. Source : "
        "[données officielles](https://www.data.gouv.fr/datasets/"
        "prix-des-carburants-en-france-flux-instantane-v2-amelioree). "
        "Noms enrichis depuis le [référentiel Chiffrex / OpenStreetMap]"
        "(https://www.data.gouv.fr/datasets/referentiel-des-noms-et-enseignes-de-stations-service-enrichi-par-openstreetmap), "
        "lorsqu'une correspondance existe. Sinon, l'adresse sert de libellé."
    )

