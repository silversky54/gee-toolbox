"""Tests for gee_toolbox.dates.dates conversion helpers."""

from datetime import datetime

import pytz

from gee_toolbox.dates import dates as gee_dates

# 2021-01-01 00:00:00 UTC in milliseconds since epoch
MS_2021_01_01 = 1_609_459_200_000


class TestEeDateToDatetime:
    def test_from_milliseconds_utc(self):
        result = gee_dates.ee_date_to_datetime(MS_2021_01_01)
        assert result == datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    def test_from_zero_milliseconds(self):
        result = gee_dates.ee_date_to_datetime(0)
        assert result == datetime(1970, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    def test_from_ee_getinfo_dict(self):
        # Shape returned by ee.Date.getInfo()
        result = gee_dates.ee_date_to_datetime({"type": "Date", "value": MS_2021_01_01})
        assert result == datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    def test_timezone_as_string(self):
        result = gee_dates.ee_date_to_datetime(MS_2021_01_01, tz="US/Eastern")
        expected = datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC).astimezone(
            pytz.timezone("US/Eastern")
        )
        assert result == expected
        assert result.tzinfo.zone == "US/Eastern"

    def test_timezone_as_pytz_object(self):
        tz = pytz.timezone("America/Los_Angeles")
        result = gee_dates.ee_date_to_datetime(MS_2021_01_01, tz=tz)
        expected = datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC).astimezone(tz)
        assert result == expected
        assert result.tzinfo.zone == "America/Los_Angeles"

    def test_dict_value_coerced_from_string(self):
        result = gee_dates.ee_date_to_datetime({"value": str(MS_2021_01_01)})
        assert result == datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)


class TestPrintEeTimestamp:
    def test_prints_datetime_from_milliseconds(self, capsys):
        gee_dates.print_ee_timestamp(MS_2021_01_01)
        captured = capsys.readouterr()
        assert captured.out.strip() == str(
            datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
        )

    def test_prints_datetime_from_dict(self, capsys):
        gee_dates.print_ee_timestamp({"value": MS_2021_01_01})
        captured = capsys.readouterr()
        assert captured.out.strip() == str(
            datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
        )

    def test_prints_with_timezone(self, capsys):
        gee_dates.print_ee_timestamp(MS_2021_01_01, tz="US/Eastern")
        captured = capsys.readouterr()
        expected = datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.UTC).astimezone(
            pytz.timezone("US/Eastern")
        )
        assert captured.out.strip() == str(expected)
