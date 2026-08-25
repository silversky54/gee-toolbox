import ee
from ee.ee_exception import EEException

from gee_toolbox.dates.dates import ee_date_to_datetime


def get_collection_dates_str(
    ee_collection: ee.imagecollection.ImageCollection,
) -> list[str]:
    """Get the dates of all images in an ImageCollection in string format 'YYYY-MM-DD'

    Images must have the property 'system:time_start'. Dates are assumed to be in UTC timezone.

    Args:
        ee_collection (ee.imagecollection.ImageCollection): ImageCollection

    Returns:
        list[str]: List of dates in format "YYYY-MM-DD"

    Raises:
        ValueError: If can't retrieve the property 'system:time_start' from the ImageCollection
    """
    try:
        image_dates_in_ms = ee_collection.aggregate_array("system:time_start").getInfo()
    except EEException as e:
        raise ValueError(
            "Couldn't get system:time_start property from image collection"
        ) from e

    if not image_dates_in_ms:
        return []

    # convert milliseconds to date strings
    collection_dates = [
        ee_date_to_datetime(date).strftime("%Y-%m-%d") for date in image_dates_in_ms
    ]
    return collection_dates
