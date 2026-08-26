"""Helper functions for managing properties in Google Earth Engine Images."""

import ee

from gee_toolbox.dates.dates import ee_date_to_datetime


def get_date_str(ee_image: ee.image.Image) -> str:
    """Return the date of an Image in string format 'YYYY-MM-DD' in UTC timezone.

    Args:
        ee_image (ee.Image): Image with 'system:time_start' property

    Returns:
        str: Date in format "YYYY-MM-DD"

    Raises:
        ValueError: If the image doesn't have the property 'system:time_start'

    """
    img_date_in_ms = ee_image.get("system:time_start").getInfo()

    if img_date_in_ms is None:
        raise ValueError("Image does not have a 'system:time_start' property")

    return ee_date_to_datetime(img_date_in_ms).strftime("%Y-%m-%d")
