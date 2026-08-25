"""Tests for gee_toolbox.assets.assets.list_assets."""

import pytest
from ee.ee_exception import EEException

from gee_toolbox.assets.assets import list_assets


@pytest.fixture
def mock_get_asset(mocker):
    return mocker.patch("gee_toolbox.assets.assets.ee.data.getAsset")


@pytest.fixture
def mock_fetch(mocker):
    return mocker.patch("gee_toolbox.assets.assets._fetch_child_assets")


class TestListAssetsValidation:
    def test_invalid_asset_types(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        with pytest.raises(ValueError):
            list_assets(
                parent="parent_folder",
                asset_types=[
                    "IMAGE",
                    "TABLE",
                    "FOLDER",
                    "IMAGE_COLLECTION",
                    "INVALID_TYPE",
                ],
            )

    def test_list_assets_non_existent(self, mock_get_asset):
        mock_get_asset.side_effect = EEException("not found")
        with pytest.raises(EEException):
            list_assets("non_existent_asset")

    def test_list_assets_invalid_parent_type(self, mock_get_asset):
        mock_get_asset.return_value = {"type": "IMAGE"}
        with pytest.raises(ValueError, match="Folder or Image Collection"):
            list_assets("invalid_parent")

    def test_list_assets_unexpected_error(self, mock_get_asset):
        mock_get_asset.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            list_assets("parent_folder")


class TestListAssetsBasic:
    def test_list_assets_empty(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.return_value = []
        assert list_assets("parent_folder") == []

    def test_list_assets_with_types(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.return_value = [
            {"name": "asset1", "type": "IMAGE"},
            {"name": "asset2", "type": "TABLE"},
        ]
        result = list_assets("parent_folder", asset_types=["IMAGE"])
        assert result == [{"name": "asset1", "type": "IMAGE"}]

    def test_list_assets_names_only(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.return_value = [
            {"name": "asset1", "type": "IMAGE"},
            {"name": "asset2", "type": "TABLE"},
        ]
        result = list_assets("parent_folder", names_only=True)
        assert result == ["asset1", "asset2"]

    def test_list_assets_inclusive(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.return_value = [{"name": "asset1", "type": "IMAGE"}]
        result = list_assets("parent_folder", inclusive=True)
        assert result == [
            {"name": "parent_folder", "type": "FOLDER"},
            {"name": "asset1", "type": "IMAGE"},
        ]

    def test_list_assets_inclusive_filtered_types(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.return_value = [
            {"name": "asset1", "type": "IMAGE"},
            {"name": "asset2", "type": "TABLE"},
        ]
        result = list_assets(
            "parent_folder", asset_types=["IMAGE", "TABLE"], inclusive=True
        )
        assert result == [
            {"name": "asset1", "type": "IMAGE"},
            {"name": "asset2", "type": "TABLE"},
        ]


class TestListAssetsRecursive:
    def test_list_assets_recursive(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_fetch.side_effect = [
            [{"name": "child_folder", "type": "FOLDER"}],
            [{"name": "asset1", "type": "IMAGE"}],
        ]
        result = list_assets("parent_folder", recursive=True)
        assert result == [
            {"name": "child_folder", "type": "FOLDER"},
            {"name": "asset1", "type": "IMAGE"},
        ]


class TestListAssetsImageCollections:
    def test_expand_image_collections(self, mock_get_asset, mock_fetch):
        mock_get_asset.side_effect = [
            {"type": "FOLDER"},
            {"type": "IMAGE_COLLECTION"},
        ]
        mock_fetch.side_effect = [
            [
                {"name": "image1", "type": "IMAGE"},
                {"name": "ic1", "type": "IMAGE_COLLECTION"},
            ],
            [{"name": "image2", "type": "IMAGE"}],
        ]
        result = list_assets("parent_folder", expand_image_collections=True)
        assert result == [
            {"name": "image1", "type": "IMAGE"},
            {"name": "ic1", "type": "IMAGE_COLLECTION"},
            {"name": "image2", "type": "IMAGE"},
        ]

    def test_image_collections_exclusively(self, mock_get_asset, mock_fetch):
        mock_get_asset.side_effect = [
            {"type": "FOLDER"},
            {"type": "IMAGE_COLLECTION"},
        ]
        mock_fetch.side_effect = [
            [
                {"name": "image1", "type": "IMAGE"},
                {"name": "ic1", "type": "IMAGE_COLLECTION"},
            ],
            [{"name": "image2", "type": "IMAGE"}],
        ]
        result = list_assets(
            "parent_folder",
            asset_types=["IMAGE"],
            expand_image_collections=True,
            image_collections_exclusively=True,
        )
        assert result == [{"name": "image2", "type": "IMAGE"}]

    def test_ic_parent_forces_expand(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "IMAGE_COLLECTION"}
        mock_fetch.return_value = [{"name": "image2", "type": "IMAGE"}]
        result = list_assets(
            "parent_ic",
            asset_types=["IMAGE_COLLECTION", "IMAGE"],
            expand_image_collections=False,
            inclusive=True,
        )
        assert result == [
            {"name": "parent_ic", "type": "IMAGE_COLLECTION"},
            {"name": "image2", "type": "IMAGE"},
        ]

    def test_ic_include_root_only(self, mock_get_asset, mock_fetch):
        mock_get_asset.return_value = {"type": "IMAGE_COLLECTION"}
        mock_fetch.return_value = [{"name": "image2", "type": "IMAGE"}]
        result = list_assets(
            "parent_ic", asset_types=["IMAGE_COLLECTION"], inclusive=True
        )
        assert result == [{"name": "parent_ic", "type": "IMAGE_COLLECTION"}]


class TestListAssetsProjectRoot:
    def test_skips_get_asset_and_omits_root(self, mock_get_asset, mock_fetch):
        mock_fetch.return_value = [
            {"name": "projects/my-project/assets/folder1", "type": "FOLDER"},
            {"name": "projects/my-project/assets/image1", "type": "IMAGE"},
        ]
        result = list_assets(
            parent="projects/my-project/assets",
            inclusive=True,
            recursive=False,
        )
        mock_get_asset.assert_not_called()
        names = [a["name"] for a in result]
        assert "projects/my-project/assets" not in names
        assert "projects/my-project/assets/folder1" in names
        assert "projects/my-project/assets/image1" in names
