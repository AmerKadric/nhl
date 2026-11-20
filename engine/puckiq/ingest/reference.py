"""Static reference data: arena coordinates, UTC offsets and brand colours.

These are geographic / branding facts, not model inputs that change. Arena
coordinates are approximate (city-level accuracy is enough for travel
distance). `utc` is the standard-time UTC offset used for time-zone shifts."""
from __future__ import annotations

TEAM_REF: dict[str, dict] = {
    "NJD": dict(lat=40.7335, lon=-74.1711, utc=-5, tz="America/New_York", color="#CE1126"),
    "NYI": dict(lat=40.7118, lon=-73.7262, utc=-5, tz="America/New_York", color="#F47D30"),
    "NYR": dict(lat=40.7505, lon=-73.9934, utc=-5, tz="America/New_York", color="#0038A8"),
    "PHI": dict(lat=39.9012, lon=-75.1720, utc=-5, tz="America/New_York", color="#F74902"),
    "PIT": dict(lat=40.4395, lon=-79.9892, utc=-5, tz="America/New_York", color="#FCB514"),
    "BOS": dict(lat=42.3662, lon=-71.0621, utc=-5, tz="America/New_York", color="#FFB81C"),
    "BUF": dict(lat=42.8750, lon=-78.8764, utc=-5, tz="America/New_York", color="#003087"),
    "MTL": dict(lat=45.4961, lon=-73.5693, utc=-5, tz="America/Toronto", color="#AF1E2D"),
    "OTT": dict(lat=45.2969, lon=-75.9272, utc=-5, tz="America/Toronto", color="#DA1A32"),
    "TOR": dict(lat=43.6435, lon=-79.3791, utc=-5, tz="America/Toronto", color="#00205B"),
    "ATL": dict(lat=33.7573, lon=-84.3963, utc=-5, tz="America/New_York", color="#5C88DA"),
    "CAR": dict(lat=35.8033, lon=-78.7220, utc=-5, tz="America/New_York", color="#CC0000"),
    "FLA": dict(lat=26.1584, lon=-80.3256, utc=-5, tz="America/New_York", color="#C8102E"),
    "TBL": dict(lat=27.9427, lon=-82.4518, utc=-5, tz="America/New_York", color="#002868"),
    "WSH": dict(lat=38.8981, lon=-77.0209, utc=-5, tz="America/New_York", color="#C8102E"),
    "CHI": dict(lat=41.8807, lon=-87.6742, utc=-6, tz="America/Chicago", color="#CF0A2C"),
    "DET": dict(lat=42.3411, lon=-83.0553, utc=-5, tz="America/Detroit", color="#CE1126"),
    "NSH": dict(lat=36.1592, lon=-86.7785, utc=-6, tz="America/Chicago", color="#FFB81C"),
    "STL": dict(lat=38.6268, lon=-90.2026, utc=-6, tz="America/Chicago", color="#002F87"),
    "CGY": dict(lat=51.0374, lon=-114.0519, utc=-7, tz="America/Edmonton", color="#D2001C"),
    "COL": dict(lat=39.7487, lon=-105.0077, utc=-7, tz="America/Denver", color="#6F263D"),
    "EDM": dict(lat=53.5469, lon=-113.4979, utc=-7, tz="America/Edmonton", color="#FF4C00"),
    "VAN": dict(lat=49.2778, lon=-123.1088, utc=-8, tz="America/Vancouver", color="#00843D"),
    "ANA": dict(lat=33.8078, lon=-117.8765, utc=-8, tz="America/Los_Angeles", color="#F47A38"),
    "DAL": dict(lat=32.7905, lon=-96.8103, utc=-6, tz="America/Chicago", color="#006847"),
    "LAK": dict(lat=34.0430, lon=-118.2673, utc=-8, tz="America/Los_Angeles", color="#A2AAAD"),
    "PHX": dict(lat=33.5319, lon=-112.2612, utc=-7, tz="America/Phoenix", color="#8C2633"),
    "ARI": dict(lat=33.5319, lon=-112.2612, utc=-7, tz="America/Phoenix", color="#8C2633"),
    "SJS": dict(lat=37.3328, lon=-121.9012, utc=-8, tz="America/Los_Angeles", color="#006D75"),
    "CBJ": dict(lat=39.9693, lon=-83.0061, utc=-5, tz="America/New_York", color="#002654"),
    "MIN": dict(lat=44.9448, lon=-93.1010, utc=-6, tz="America/Chicago", color="#154734"),
    "WPG": dict(lat=49.8928, lon=-97.1436, utc=-6, tz="America/Winnipeg", color="#041E42"),
    "VGK": dict(lat=36.1029, lon=-115.1785, utc=-8, tz="America/Los_Angeles", color="#B4975A"),
    "SEA": dict(lat=47.6221, lon=-122.3540, utc=-8, tz="America/Los_Angeles", color="#99D9D9"),
    "UTA": dict(lat=40.7683, lon=-111.9011, utc=-7, tz="America/Denver", color="#71AFE5"),
}

# The Utah club was founded on Arizona's hockey operations (players, staff),
# so team-strength ratings carry over from franchise 28 to franchise 40.
FRANCHISE_CONTINUITY = {40: 28}

DATA_SOURCES = [
    dict(key="nhl_games", name="NHL Stats API — games", url="https://api.nhle.com/stats/rest/en/game",
         license="Publicly accessible, undocumented. No published terms; used read-only at low request rates.",
         attribution="Schedule, results and live game state: NHL.", status="active"),
    dict(key="nhl_goalies", name="NHL Stats API — goalie game logs",
         url="https://api.nhle.com/stats/rest/en/goalie/summary",
         license="Publicly accessible, undocumented. No published terms; used read-only at low request rates.",
         attribution="Goaltender starts and saves: NHL.", status="active"),
    dict(key="nhl_web", name="NHL Web API — schedule, scores, boxscores", url="https://api-web.nhle.com/v1/",
         license="Publicly accessible, undocumented. No published terms; used read-only at low request rates.",
         attribution="Schedule, results and live game state: NHL.", status="active"),
    dict(key="moneypuck_teams", name="MoneyPuck — team game-by-game data",
         url="https://moneypuck.com/data.htm",
         license="Free for non-commercial use with clear credit to MoneyPuck.com. Other uses require permission.",
         attribution="Expected goals and shot data: MoneyPuck.com.", status="active"),
    dict(key="moneypuck_shots", name="MoneyPuck — shot-level data", url="https://moneypuck.com/data.htm",
         license="Free for non-commercial use with clear credit to MoneyPuck.com. Other uses require permission.",
         attribution="Expected goals and shot data: MoneyPuck.com.", status="active"),
    dict(key="injuries", name="Injuries and confirmed lineups", url="",
         license="No free, licensed source has been integrated.",
         attribution="Not available.", status="unavailable"),
]
