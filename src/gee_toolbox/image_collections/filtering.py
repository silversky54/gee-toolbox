import ee


def _ee_set_date_as_property(image: ee.image.Image) -> ee.image.Image:
    """Sets a date property named 'simpleDate' with the image's date in string format YYYY-MM-dd

    Args:
        image (ee.image.Image): Image to set the date property

    Returns:
        ee.Image

    """
    date = ee.ee_date.Date(image.date().format("YYYY-MM-dd"))
    return ee.image.Image(
        image.set("simpleDate", date)
    )  # Wrapping in ee.Image to avoid cast error


def _ee_remove_date_property(image):
    return (
        ee.image.Image()  # Image without any bands or properties
        .addBands(image)  # add bands
        .copyProperties(
            source=image, exclude=["simpleDate"]
        )  # add properties excluding simpleTime
    )


def ee_filter_ic_by_dates(
    ee_collection: ee.imagecollection.ImageCollection, dates_list: list[str]
) -> ee.imagecollection.ImageCollection:
    """Filters an ImageCollection by a list of dates

    Args:
        ee_collection: ee.ImageCollection to filter
        dates_list: list of dates in format "YYYY-MM-DD"

    Returns:
        ee.ImageCollection
    """

    # add property with image date in string format "YYYY-MM-DD"
    ee_collection = ee_collection.map(_ee_set_date_as_property)

    # create ee.List of dates
    ee_dates_list = ee.ee_list.List([ee.ee_date.Date(i_date) for i_date in dates_list])

    # filter collection by dates
    ee_filtered_ic = ee_collection.filter(
        ee.filter.Filter.inList("simpleDate", ee_dates_list)
    )

    # remove simpleDate property
    ee_filtered_ic = ee_filtered_ic.map(_ee_remove_date_property)

    return ee_filtered_ic
