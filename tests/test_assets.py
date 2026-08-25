"""Tests for gee_toolbox.assets.assets helpers and existence checks.

Excludes tests for listing and pruning assets. see separate test files for those.

"""

from pathlib import Path

import pytest
from ee.ee_exception import EEException

from gee_toolbox.assets import assets as gee_assets
from gee_toolbox.assets.assets import ASSET_TYPES


class TestRequestDelConfirmation:
    def test_confirm_delete_yes(self, mocker):
        mocker.patch("builtins.input", return_value="y")
        assert gee_assets._request_del_confirmation() is True

    def test_confirm_delete_no(self, mocker):
        mocker.patch("builtins.input", return_value="n")
        assert gee_assets._request_del_confirmation() is False

    def test_confirm_delete_empty(self, mocker):
        mocker.patch("builtins.input", return_value="")
        assert gee_assets._request_del_confirmation() is False

    def test_confirm_delete_invalid_then_yes(self, mocker):
        mocker.patch("builtins.input", side_effect=["invalid", "y"])
        assert gee_assets._request_del_confirmation() is True

    def test_confirm_delete_invalid_then_no(self, mocker):
        mocker.patch("builtins.input", side_effect=["invalid", "n"])
        assert gee_assets._request_del_confirmation() is False


class TestMakeDelWarning:
    def test_make_del_warning_no_assets(self):
        asset = "test_asset"
        expected = (
            "******************************************************\n"
            "WARNING\n"
            "******************************************************\n"
            "You are about to delete the following assets:\n"
            "-Images: 0\n"
            "-Image Collections: 0\n"
            "-Tables: 0\n"
            "-Folders: 0\n"
            f"Target: {asset}\n"
        )
        assert gee_assets._make_del_warning(asset, []) == expected

    def test_make_del_warning_with_assets(self):
        asset = "test_asset"
        objects_list = ["IMAGE", "IMAGE_COLLECTION", "TABLE", "FOLDER", "IMAGE"]
        expected = (
            "******************************************************\n"
            "WARNING\n"
            "******************************************************\n"
            "You are about to delete the following assets:\n"
            "-Images: 2\n"
            "-Image Collections: 1\n"
            "-Tables: 1\n"
            "-Folders: 1\n"
            f"Target: {asset}\n"
        )
        assert gee_assets._make_del_warning(asset, objects_list) == expected

    def test_make_del_warning_dry_run(self):
        asset = "test_asset"
        text = gee_assets._make_del_warning(asset, ["IMAGE"], dry_run=True)
        assert "would delete" in text
        assert "are about to delete" not in text

    def test_make_del_warning_only_images(self):
        asset = "test_asset"
        objects_list = ["IMAGE", "IMAGE", "IMAGE"]
        expected = (
            "******************************************************\n"
            "WARNING\n"
            "******************************************************\n"
            "You are about to delete the following assets:\n"
            "-Images: 3\n"
            "-Image Collections: 0\n"
            "-Tables: 0\n"
            "-Folders: 0\n"
            f"Target: {asset}\n"
        )
        assert gee_assets._make_del_warning(asset, objects_list) == expected

    def test_make_del_warning_only_folders(self):
        asset = "test_asset"
        objects_list = ["FOLDER", "FOLDER"]
        expected = (
            "******************************************************\n"
            "WARNING\n"
            "******************************************************\n"
            "You are about to delete the following assets:\n"
            "-Images: 0\n"
            "-Image Collections: 0\n"
            "-Tables: 0\n"
            "-Folders: 2\n"
            f"Target: {asset}\n"
        )
        assert gee_assets._make_del_warning(asset, objects_list) == expected


class TestCheckAssetTypes:
    def test_check_asset_types_no_input(self):
        assert gee_assets._check_asset_types("") == list(ASSET_TYPES)
        assert gee_assets._check_asset_types(None) == list(ASSET_TYPES)

    def test_check_asset_types_single_valid_type(self):
        assert gee_assets._check_asset_types("image") == ["IMAGE"]

    def test_check_asset_types_multiple_valid_types(self):
        assert gee_assets._check_asset_types(["image", "table"]) == ["IMAGE", "TABLE"]

    def test_check_asset_types_invalid_type(self):
        with pytest.raises(
            ValueError, match=r"Invalid asset type\(s\)\. Must be one of .*"
        ):
            gee_assets._check_asset_types("invalid_type")

    def test_check_asset_types_mixed_valid_invalid_types(self):
        with pytest.raises(
            ValueError, match=r"Invalid asset type\(s\)\. Must be one of .*"
        ):
            gee_assets._check_asset_types(["image", "invalid_type"])

    def test_check_asset_types_empty_list(self):
        assert gee_assets._check_asset_types([]) == list(ASSET_TYPES)


class TestGetAssetNames:
    def test_get_asset_names(self):
        assets = [
            {"name": "name1", "type": "IMAGE"},
            {"name": "name2", "type": "IMAGE"},
        ]
        assert gee_assets._get_asset_names(assets) == ["name1", "name2"]

    def test_get_asset_names_empty(self):
        assert gee_assets._get_asset_names([]) == []

    def test_get_asset_names_no_name(self):
        assets = [{"type": "IMAGE"}, {"name": "name2", "type": "IMAGE"}]
        assert gee_assets._get_asset_names(assets) == ["name2"]

    def test_get_asset_names_no_assets(self):
        with pytest.raises(TypeError):
            gee_assets._get_asset_names(None)  # type: ignore[arg-type]


class TestGetAssetTypes:
    def test_get_asset_types(self):
        assets = [
            {"name": "name1", "type": "IMAGE"},
            {"name": "name2", "type": "IMAGE"},
        ]
        assert gee_assets._get_asset_types(assets) == ["IMAGE", "IMAGE"]

    def test_get_asset_types_empty(self):
        assert gee_assets._get_asset_types([]) == []

    def test_get_asset_types_no_type(self):
        assets = [{"name": "name1"}, {"name": "name2", "type": "IMAGE"}]
        assert gee_assets._get_asset_types(assets) == ["IMAGE"]

    def test_get_asset_types_no_assets(self):
        with pytest.raises(TypeError):
            gee_assets._get_asset_types(None)  # type: ignore[arg-type]


class TestIsProjectRoot:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("projects/my-project/assets", True),
            ("projects/my-project/assets/", True),
            ("projects/ee-chompitest/assets", True),
            ("projects/my-project/assets/folder", False),
            ("projects/my-project/assets/folder/image", False),
            ("projects/my-project", False),
            ("users/someone", False),
            ("folder", False),
        ],
    )
    def test_is_project_root(self, path, expected):
        assert gee_assets._is_project_root(path) is expected

    def test_is_project_root_path_object(self):
        assert gee_assets._is_project_root(Path("projects/my-project/assets")) is True


class TestFetchChildAssets:
    def test_fetch_with_page_size_none_single_call(self, mocker):
        mock_list = mocker.patch(
            "gee_toolbox.assets.assets.ee.data.listAssets",
            return_value={"assets": [{"name": "a", "type": "IMAGE"}]},
        )
        result = gee_assets._fetch_child_assets("parent", page_size=None)
        mock_list.assert_called_once_with({"parent": "parent"})
        assert result == [{"name": "a", "type": "IMAGE"}]

    def test_fetch_paginates_until_no_token(self, mocker):
        mock_list = mocker.patch(
            "gee_toolbox.assets.assets.ee.data.listAssets",
            side_effect=[
                {
                    "assets": [{"name": "a1", "type": "IMAGE"}],
                    "nextPageToken": "tok2",
                },
                {
                    "assets": [{"name": "a2", "type": "IMAGE"}],
                    "nextPageToken": "",
                },
            ],
        )
        result = gee_assets._fetch_child_assets("parent", page_size=1)
        assert result == [
            {"name": "a1", "type": "IMAGE"},
            {"name": "a2", "type": "IMAGE"},
        ]
        assert mock_list.call_count == 2
        assert mock_list.call_args_list[0].args[0] == {
            "parent": "parent",
            "pageSize": 1,
        }
        assert mock_list.call_args_list[1].args[0] == {
            "parent": "parent",
            "pageSize": 1,
            "pageToken": "tok2",
        }

    def test_fetch_empty_assets_key(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.listAssets",
            return_value={},
        )
        assert gee_assets._fetch_child_assets("parent", page_size=10) == []


class TestCheckAssetExists:
    def test_exists_without_type(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "IMAGE"},
        )
        assert gee_assets.check_asset_exists("projects/p/assets/img") is True

    def test_exists_matching_type(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "IMAGE"},
        )
        assert gee_assets.check_asset_exists("projects/p/assets/img", "image") is True

    def test_exists_wrong_type(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "TABLE"},
        )
        assert gee_assets.check_asset_exists("projects/p/assets/t", "IMAGE") is False

    def test_missing_asset_returns_false(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            side_effect=EEException("not found"),
        )
        assert gee_assets.check_asset_exists("projects/p/assets/missing") is False

    def test_empty_asset_info_returns_false(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={},
        )
        assert gee_assets.check_asset_exists("projects/p/assets/empty") is False

    def test_invalid_asset_type_raises(self):
        with pytest.raises(ValueError, match="Invalid asset type"):
            gee_assets.check_asset_exists("projects/p/assets/x", "NOT_A_TYPE")


class TestCheckContainerExists:
    def test_folder_is_container(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "FOLDER"},
        )
        assert gee_assets.check_container_exists("projects/p/assets/folder") is True

    def test_image_collection_is_container(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "IMAGE_COLLECTION"},
        )
        assert gee_assets.check_container_exists("projects/p/assets/ic") is True

    def test_image_is_not_container(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "IMAGE"},
        )
        assert gee_assets.check_container_exists("projects/p/assets/img") is False

    def test_missing_returns_false(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            side_effect=EEException("not found"),
        )
        assert gee_assets.check_container_exists("projects/p/assets/missing") is False

    def test_empty_asset_info_returns_false(self, mocker):
        mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={},
        )
        assert gee_assets.check_container_exists("projects/p/assets/empty") is False

    def test_path_object_accepted(self, mocker):
        mock_get = mocker.patch(
            "gee_toolbox.assets.assets.ee.data.getAsset",
            return_value={"type": "FOLDER"},
        )
        path = Path("projects/p/assets/folder")
        assert gee_assets.check_container_exists(path) is True
        mock_get.assert_called_once_with(path.as_posix())
