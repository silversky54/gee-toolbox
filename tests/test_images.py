"""Tests for gee_toolbox.images helpers."""

import pytest

from gee_toolbox.images.properties import get_date_str

# 2021-01-01 00:00:00 UTC in milliseconds since epoch
MS_2021_01_01 = 1_609_459_200_000
# 2021-06-15 12:00:00 UTC
MS_2021_06_15 = 1_623_758_400_000


class TestGetDateStr:
    def test_returns_yyyy_mm_dd(self, mocker):
        ee_image = mocker.Mock()
        ee_image.get.return_value.getInfo.return_value = MS_2021_01_01

        assert get_date_str(ee_image) == "2021-01-01"
        ee_image.get.assert_called_once_with("system:time_start")

    def test_returns_midday_date_in_utc(self, mocker):
        ee_image = mocker.Mock()
        ee_image.get.return_value.getInfo.return_value = MS_2021_06_15

        assert get_date_str(ee_image) == "2021-06-15"

    def test_raises_when_time_start_missing(self, mocker):
        ee_image = mocker.Mock()
        ee_image.get.return_value.getInfo.return_value = None

        with pytest.raises(
            ValueError, match="Image does not have a 'system:time_start' property"
        ):
            get_date_str(ee_image)
