"""Tests for gee_toolbox.image_collections helpers."""

import ee
import ee.data as ee_data
import pytest
from ee import apitestcase
from ee.ee_exception import EEException

from gee_toolbox.image_collections import filtering as gee_filtering
from gee_toolbox.image_collections.properties import get_collection_dates_str

# 2021-01-01 00:00:00 UTC in milliseconds since epoch
MS_2021_01_01 = 1_609_459_200_000
# 2021-06-15 12:00:00 UTC
MS_2021_06_15 = 1_623_758_400_000


@pytest.fixture
def ee_offline():
    """Initialize Earth Engine offline via apitestcase stubs."""
    ee.Reset()
    ee_data._install_cloud_api_resource = lambda: None
    ee_data.getAlgorithms = apitestcase.GetAlgorithms
    ee_data.computeValue = lambda x: {"value": "fakeValue"}
    ee.Initialize(None, "", project="test-project")


class TestGetCollectionDatesStr:
    def test_returns_date_strings(self, mocker):
        ee_collection = mocker.Mock()
        ee_collection.aggregate_array.return_value.getInfo.return_value = [
            MS_2021_01_01,
            MS_2021_06_15,
        ]

        assert get_collection_dates_str(ee_collection) == [
            "2021-01-01",
            "2021-06-15",
        ]
        ee_collection.aggregate_array.assert_called_once_with("system:time_start")

    def test_empty_list_when_no_dates(self, mocker):
        ee_collection = mocker.Mock()
        ee_collection.aggregate_array.return_value.getInfo.return_value = []

        assert get_collection_dates_str(ee_collection) == []

    def test_empty_list_when_getinfo_returns_none(self, mocker):
        ee_collection = mocker.Mock()
        ee_collection.aggregate_array.return_value.getInfo.return_value = None

        assert get_collection_dates_str(ee_collection) == []

    def test_raises_value_error_on_ee_exception(self, mocker):
        ee_collection = mocker.Mock()
        ee_collection.aggregate_array.return_value.getInfo.side_effect = EEException(
            "missing property"
        )

        with pytest.raises(
            ValueError,
            match="Couldn't get system:time_start property from image collection",
        ):
            get_collection_dates_str(ee_collection)


class TestEeSetDateAsProperty:
    def test_returns_image(self, ee_offline):
        image = ee.Image(ee.Image(1).set("system:time_start", MS_2021_01_01))
        result = gee_filtering._ee_set_date_as_property(image)

        assert isinstance(result, ee.Image)

    def test_serialized_expression_sets_simple_date(self, ee_offline):
        image = ee.Image(ee.Image(1).set("system:time_start", MS_2021_01_01))
        serialized = gee_filtering._ee_set_date_as_property(image).serialize()

        assert "simpleDate" in serialized
        assert "Element.set" in serialized
        assert "Date" in serialized


class TestEeRemoveDateProperty:
    def test_serialized_expression_excludes_simple_date(self, ee_offline):
        image = ee.Image(1).set("simpleDate", ee.Date("2021-01-01"))
        serialized = gee_filtering._ee_remove_date_property(image).serialize()

        assert "Image.copyProperties" in serialized
        assert "simpleDate" in serialized
        assert "Image.addBands" in serialized


class TestEeFilterIcByDates:
    def test_returns_image_collection(self, ee_offline):
        collection = ee.ImageCollection(
            [ee.Image(1).set("system:time_start", MS_2021_01_01)]
        )
        result = gee_filtering.ee_filter_ic_by_dates(collection, ["2021-01-01"])

        assert isinstance(result, ee.ImageCollection)

    def test_serialized_expression_filters_by_simple_date(self, ee_offline):
        collection = ee.ImageCollection(
            [
                ee.Image(1).set("system:time_start", MS_2021_01_01),
                ee.Image(2).set("system:time_start", MS_2021_06_15),
            ]
        )
        serialized = gee_filtering.ee_filter_ic_by_dates(
            collection, ["2021-01-01", "2021-06-15"]
        ).serialize()

        assert "Collection.filter" in serialized
        assert "Filter.listContains" in serialized
        assert "simpleDate" in serialized
        assert "Collection.map" in serialized
        assert "2021-01-01" in serialized
        assert "2021-06-15" in serialized

    def test_accepts_empty_dates_list(self, ee_offline):
        collection = ee.ImageCollection(
            [ee.Image(1).set("system:time_start", MS_2021_01_01)]
        )
        result = gee_filtering.ee_filter_ic_by_dates(collection, [])

        assert isinstance(result, ee.ImageCollection)
        assert "Filter.listContains" in result.serialize()
