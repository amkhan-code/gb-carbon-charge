"""Project-wide settings: paths, the leakage cutoff, and data source parameters."""

import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

DATA_DIR = Path(os.environ.get("CARBON_CHARGE_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "carbon.duckdb"

# Forecasts for day D are made at this UK local time on D-1.
CUTOFF_TIME = time(11, 0)

HISTORY_START = date(2023, 1, 1)
# Open-Meteo's Previous Runs archive has nothing for our variables before early
# 2024 (see DATA_ISSUES.md); rows that come back all-null are dropped.
WEATHER_HISTORY_START = date(2024, 1, 1)

CARBON_INTENSITY_URL = "https://api.carbonintensity.org.uk"
NESO_CKAN_URL = "https://api.neso.energy/api/3/action"
# "Day Ahead Half Hourly Demand Forecast Performance"
NESO_DEMAND_RESOURCE_ID = "08e41551-80f8-4e28-a416-ea473a695db9"
OPEN_METEO_PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

WEATHER_MODEL = "ecmwf_ifs025"
# Forecast vintages to store, as Open-Meteo "previous_dayN" offsets.
WEATHER_LEAD_DAYS = (1, 2)
# Open-Meteo variable name -> stored column name.
WEATHER_VARIABLES = {
    "temperature_2m": "temperature_2m_c",
    "wind_speed_10m": "wind_speed_10m_ms",
    "wind_speed_100m": "wind_speed_100m_ms",
    "shortwave_radiation": "shortwave_radiation_wm2",
    "cloud_cover": "cloud_cover_pct",
}


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    role: str


WEATHER_LOCATIONS = (
    Location("north_sea_dogger", 54.0, 1.9, "offshore wind"),
    Location("east_anglia_offshore", 52.3, 2.0, "offshore wind"),
    Location("moray_firth", 58.1, -2.8, "offshore wind"),
    Location("irish_sea", 53.8, -3.6, "offshore wind"),
    Location("scottish_borders", 55.5, -3.5, "onshore wind"),
    Location("south_west", 50.9, -3.2, "solar"),
    Location("east_midlands", 52.6, -0.3, "solar"),
    Location("london", 51.5, -0.1, "demand"),
    Location("manchester", 53.5, -2.2, "demand"),
)


# --- Availability assumptions (leakage rules) -------------------------------
# A value is only usable once it has been PUBLISHED. These lags are assumptions,
# chosen conservatively; see DATA_ISSUES.md (X-1, WX-2).
# Carbon intensity actuals: available this long after the END of their period.
CI_ACTUAL_LAG_MINUTES = 60
# ECMWF IFS runs become available on Open-Meteo this long after initialisation.
WEATHER_RUN_LAG_HOURS = 8
# Only this lead is used for features: previous_day1 values for late target
# hours come from runs published after the cutoff.
WEATHER_FEATURE_LEAD_DAYS = 2

REPORTS_DIR = Path(os.environ.get("CARBON_CHARGE_REPORTS_DIR", "reports"))


# --- Layer 2: wholesale prices ------------------------------------------------
# "N2EX GB Day-Ahead Price" on the NESO data portal (hourly, GBP/MWh).
NESO_DAY_AHEAD_PRICE_RESOURCE_ID = "4f27eea5-7038-4f73-9740-e3e4ad47c26a"
# Elexon BMRS Market Index Data (half-hourly). N2EXMIDP rows are all zero; APXMIDP carries the price.
ELEXON_MID_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
MID_PROVIDER = "APXMIDP"
# The N2EX day-ahead auction closes 09:50 and publishes by 10:00 (GMT) on D-1 (Nord Pool
# documentation). Modelled as 10:00 UTC on D-1, i.e. exactly the 11:00 UK cutoff in summer.
DAY_AHEAD_PRICE_PUBLISHED_UTC = time(10, 0)
# MID prices are usable this long after the END of their period (assumption, like CI actuals).
MID_LAG_MINUTES = 60
