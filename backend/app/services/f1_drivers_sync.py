import logging
import httpx
from sqlalchemy.orm import Session
from app.models.f1_drivers import F1Driver

logger = logging.getLogger(__name__)


def fetch_openf1_drivers(client: httpx.Client) -> list[dict]:
    # 1. GET sessions to find current year
    resp_sessions = client.get("https://api.openf1.org/v1/sessions?session_key=latest", timeout=15.0)
    resp_sessions.raise_for_status()
    sessions_data = resp_sessions.json()
    if not sessions_data:
        raise ValueError("No session data returned from OpenF1")
    season = sessions_data[0].get("year")
    if not season:
        raise ValueError("No year in OpenF1 session data")

    # 2. GET drivers for latest session
    resp_drivers = client.get("https://api.openf1.org/v1/drivers?session_key=latest", timeout=15.0)
    resp_drivers.raise_for_status()
    drivers_data = resp_drivers.json()

    result = []
    for d in drivers_data:
        driver_number = d.get("driver_number")
        team_name = d.get("team_name")
        last_name = d.get("last_name")

        # Skip rows missing number, team, or surname
        if driver_number is None or not team_name or not last_name:
            continue

        raw_colour = d.get("team_colour") or ""
        team_colour = f"#{raw_colour.lstrip('#').upper()}" if raw_colour else "#000000"

        full_name_raw = d.get("full_name") or ""
        full_name = full_name_raw.title() if full_name_raw else None

        result.append({
            "season": int(season),
            "surname": str(last_name).upper(),
            "full_name": full_name,
            "number": int(driver_number),
            "team_name": str(team_name),
            "team_colour": team_colour,
        })

    return result


def sync_f1_drivers(db: Session, drivers: list[dict]) -> dict:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}

    for item in drivers:
        season = item["season"]
        surname = item["surname"]

        row = db.query(F1Driver).filter(
            F1Driver.season == season,
            F1Driver.surname == surname,
        ).first()

        if row is None:
            new_row = F1Driver(
                season=season,
                surname=surname,
                full_name=item["full_name"],
                number=item["number"],
                team_name=item["team_name"],
                team_colour=item["team_colour"],
                team_logo_path=None,
                verified=True,
            )
            db.add(new_row)
            counts["inserted"] += 1
        else:
            changed = False
            if row.full_name != item["full_name"]:
                row.full_name = item["full_name"]
                changed = True
            if row.number != item["number"]:
                row.number = item["number"]
                changed = True
            if row.team_name != item["team_name"]:
                row.team_name = item["team_name"]
                changed = True
            if row.team_colour != item["team_colour"]:
                row.team_colour = item["team_colour"]
                changed = True
            if not row.verified:
                row.verified = True
                changed = True

            # Note: NEVER overwrite a non-null team_logo_path.
            if changed:
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1

    db.commit()
    return counts
