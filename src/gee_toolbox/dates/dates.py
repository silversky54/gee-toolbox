"""Helper functions for Google Earth Engine Dates Management.

Functions to manage dates either within GEE or to convert between GEE and Python.
"""

from datetime import datetime

import ee.ee_date
import pytz

UTC_TZ = pytz.timezone("UTC")


def ee_date_to_datetime(
    dt: ee.ee_date.Date | dict | int, tz: str | pytz.tzinfo.BaseTzInfo = "UTC"
) -> datetime:
    """Convert ee.Date,ee.Date.getInfo() or milliseconds since epoch to a datetime object.

    Args:
        dt: ee.Date, dict (fro ee.Date.getInfo()) or milliseconds since epoch
        tz: The timezone (default is UTC)

    Returns:
        datetime: The datetime object in the given timezone

    """
    if isinstance(dt, ee.ee_date.Date):
        info = dt.getInfo()
        if info is None:
            raise ValueError("Date is None")
        dt = int(info["value"])

    if isinstance(dt, dict):
        dt = int(dt["value"])

    if isinstance(tz, str):
        tz = pytz.timezone(tz)
    return datetime.fromtimestamp(dt / 1000, tz)


def print_ee_timestamp(
    dt: dict | int, tz: str | pytz.tzinfo.BaseTzInfo = "UTC"
) -> None:
    """Print the datetime of ee.Date.getInfo() or milliseconds since epoch.

    Args:
        dt: Date retrieved from GEE using ee.Date.getInfo() or milliseconds since epoch
        tz: Timezone (default is UTC)

    Returns:
        None

    """
    print(ee_date_to_datetime(dt, tz))
