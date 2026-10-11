import argparse
import sys
import logging
from datetime import datetime, timezone
import zoneinfo

# Add the parent directory to sys.path so we can import app
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.database import SessionLocal
from app.models.target_fanpages import TargetFanpage

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def convert_hour(hour: int, from_tz: zoneinfo.ZoneInfo, to_tz: zoneinfo.ZoneInfo, now_utc: datetime) -> int:
    """Converts an hour from one timezone to another at the current time."""
    now_from = now_utc.astimezone(from_tz)
    dt_from = now_from.replace(hour=hour, minute=0, second=0, microsecond=0)
    dt_to = dt_from.astimezone(to_tz)
    return dt_to.hour

def plan_fanpage(name: str, current_tz: str | None, start: int | None, end: int | None, now_utc: datetime):
    name_lower = name.lower()
    if "fight today" in name_lower:
        new_tz_str = "America/New_York"
        new_country = "US"
    elif "gp weekend" in name_lower:
        new_tz_str = "Europe/Paris"
        new_country = "FR"
    else:
        new_tz_str = "Europe/London"
        new_country = "GB"

    if not current_tz:
        current_tz = "Asia/Jakarta"
    try:
        from_tz = zoneinfo.ZoneInfo(current_tz)
    except zoneinfo.ZoneInfoNotFoundError:
        from_tz = zoneinfo.ZoneInfo("Asia/Jakarta")
        current_tz = "Asia/Jakarta"
        
    to_tz = zoneinfo.ZoneInfo(new_tz_str)
    
    if start is not None and end is not None:
        new_start = convert_hour(start, from_tz, to_tz, now_utc)
        new_end = convert_hour(end, from_tz, to_tz, now_utc)
    else:
        new_start = None
        new_end = None
        
    return new_tz_str, new_country, new_start, new_end

def main(argv=None):
    parser = argparse.ArgumentParser(description="Set fanpage timezones and convert sleep hours")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database (dry-run by default)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        fanpages = db.query(TargetFanpage).all()
        now_utc = datetime.now(timezone.utc)
        
        for fp in fanpages:
            new_tz, new_country, new_start, new_end = plan_fanpage(
                fp.name, fp.timezone, fp.publish_sleep_start_hour, fp.publish_sleep_end_hour, now_utc
            )
            
            old_tz = fp.timezone or "Asia/Jakarta"
            
            changed = (fp.timezone != new_tz) or (fp.target_country != new_country) or (fp.publish_sleep_start_hour != new_start) or (fp.publish_sleep_end_hour != new_end)
            
            logger.info("Fanpage: %s", fp.name)
            if not changed:
                logger.info("  (unchanged)")
            else:
                logger.info("  Mapping: %s -> %s (%s)", old_tz, new_tz, new_country)
                if fp.publish_sleep_start_hour is not None:
                    logger.info("  Sleep window: %s-%s %s -> %s-%s %s", fp.publish_sleep_start_hour, fp.publish_sleep_end_hour, old_tz, new_start, new_end, new_tz)
                else:
                    logger.info("  Sleep window: no sleep window")

            if args.apply and changed:
                fp.timezone = new_tz
                fp.target_country = new_country
                fp.publish_sleep_start_hour = new_start
                fp.publish_sleep_end_hour = new_end

        if args.apply:
            db.commit()
            logger.info("Changes applied.")
        else:
            logger.info("Dry-run complete. Use --apply to save changes.")
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
