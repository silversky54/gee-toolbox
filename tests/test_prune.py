"""Tests for gee_toolbox.assets.assets.prune."""

import pytest
from ee.ee_exception import EEException

from gee_toolbox.assets.assets import prune


@pytest.fixture
def mock_get_asset(mocker):
    return mocker.patch("gee_toolbox.assets.assets.ee.data.getAsset")


@pytest.fixture
def mock_list_assets(mocker):
    return mocker.patch("gee_toolbox.assets.assets.list_assets")


@pytest.fixture
def mock_delete(mocker):
    return mocker.patch("gee_toolbox.assets.assets.ee.data.deleteAsset")


@pytest.fixture
def mock_tqdm(mocker):
    return mocker.patch("gee_toolbox.assets.assets.tqdm", side_effect=lambda x: x)


@pytest.fixture
def mock_confirm(mocker):
    return mocker.patch("gee_toolbox.assets.assets._request_del_confirmation")


class TestPruneValidation:
    def test_non_existent_asset(self, mock_get_asset):
        mock_get_asset.side_effect = EEException("not found")
        with pytest.raises(EEException):
            prune("non_existent_asset")

    def test_folder_requires_all_asset_types(self, mock_get_asset):
        mock_get_asset.return_value = {"type": "FOLDER"}
        with pytest.raises(ValueError, match="Can't delete 'FOLDER'"):
            prune(
                "folder_path",
                asset_types=["FOLDER"],
                recursive=True,
                expand_image_collections=True,
                silent=True,
            )

    def test_folder_requires_recursive_and_expand(self, mock_get_asset):
        mock_get_asset.return_value = {"type": "FOLDER"}
        with pytest.raises(
            ValueError,
            match="recursive=True and expand_image_collections=True",
        ):
            prune(
                "folder_path",
                asset_types=[
                    "FOLDER",
                    "IMAGE",
                    "TABLE",
                    "IMAGE_COLLECTION",
                ],
                recursive=False,
                expand_image_collections=False,
                silent=True,
            )

    def test_image_collection_requires_expand(self, mock_get_asset):
        mock_get_asset.return_value = {"type": "IMAGE_COLLECTION"}
        with pytest.raises(
            ValueError,
            match="expand_image_collections=True",
        ):
            prune(
                "image_collection_path",
                asset_types=["IMAGE_COLLECTION"],
                expand_image_collections=False,
                silent=True,
            )


class TestPruneSingleAsset:
    def test_prune_single_image(self, mock_get_asset, mock_delete, mock_tqdm):
        asset_path = "projects/p/assets/img"
        mock_get_asset.return_value = {"type": "IMAGE"}

        result = prune(asset=asset_path, silent=True)

        assert result == {"deleted": [asset_path], "failed": [], "skipped": []}
        mock_delete.assert_called_once_with(asset_path)

    def test_prune_type_not_in_asset_types_returns_empty(
        self, mock_get_asset, mock_delete
    ):
        mock_get_asset.return_value = {"type": "IMAGE"}
        result = prune(asset="projects/p/assets/img", asset_types=["TABLE"], silent=True)
        assert result == {"deleted": [], "failed": [], "skipped": []}
        mock_delete.assert_not_called()


class TestPruneContainer:
    def test_prune_folder_recursive(
        self,
        mock_get_asset,
        mock_list_assets,
        mock_delete,
        mock_tqdm,
    ):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_list_assets.return_value = [
            {"name": "folder_path/image1", "type": "IMAGE"},
            {"name": "folder_path/subfolder", "type": "FOLDER"},
            {"name": "folder_path", "type": "FOLDER"},
        ]

        result = prune(
            "folder_path",
            asset_types=["FOLDER", "IMAGE", "TABLE", "IMAGE_COLLECTION"],
            recursive=True,
            expand_image_collections=True,
            silent=True,
        )

        assert set(result["deleted"]) == {
            "folder_path/image1",
            "folder_path/subfolder",
            "folder_path",
        }
        assert result["failed"] == []
        assert result["skipped"] == []
        # deeper paths deleted before shallower ones
        deleted_order = [c.args[0] for c in mock_delete.call_args_list]
        assert deleted_order.index("folder_path/image1") < deleted_order.index(
            "folder_path"
        )
        assert deleted_order.index("folder_path/subfolder") < deleted_order.index(
            "folder_path"
        )

    def test_prune_image_collection(
        self,
        mock_get_asset,
        mock_list_assets,
        mock_delete,
        mock_tqdm,
    ):
        mock_get_asset.return_value = {"type": "IMAGE_COLLECTION"}
        mock_list_assets.return_value = [
            {"name": "ic/image1", "type": "IMAGE"},
            {"name": "ic", "type": "IMAGE_COLLECTION"},
        ]

        result = prune(
            "ic",
            asset_types=["IMAGE_COLLECTION"],
            expand_image_collections=True,
            silent=True,
        )

        assert set(result["deleted"]) == {"ic/image1", "ic"}
        # IMAGE auto-added with IC-exclusive mode when IMAGE omitted
        assert mock_list_assets.call_args.kwargs["image_collections_exclusively"] is True
        assert "IMAGE" in mock_list_assets.call_args.kwargs["asset_types"]


class TestPruneDryRunAndConfirm:
    def test_dry_run_skips_delete(
        self, mock_get_asset, mock_list_assets, mock_delete, mock_confirm
    ):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_list_assets.return_value = [
            {"name": "folder_path/image1", "type": "IMAGE"},
            {"name": "folder_path/subfolder", "type": "FOLDER"},
        ]

        result = prune(
            "folder_path",
            asset_types=["FOLDER", "IMAGE", "TABLE", "IMAGE_COLLECTION"],
            recursive=True,
            expand_image_collections=True,
            dry_run=True,
        )

        assert result == {
            "deleted": [],
            "failed": [],
            "skipped": ["folder_path/image1", "folder_path/subfolder"],
        }
        mock_delete.assert_not_called()
        mock_confirm.assert_not_called()

    def test_cancelled_confirmation_skips(
        self, mock_get_asset, mock_list_assets, mock_delete, mock_confirm
    ):
        mock_get_asset.return_value = {"type": "IMAGE"}
        mock_confirm.return_value = False

        result = prune(asset="projects/p/assets/img", silent=False)

        assert result == {
            "deleted": [],
            "failed": [],
            "skipped": ["projects/p/assets/img"],
        }
        mock_delete.assert_not_called()

    def test_empty_list_returns_early(self, mock_get_asset, mock_list_assets, mock_delete):
        mock_get_asset.return_value = {"type": "FOLDER"}
        mock_list_assets.return_value = []
        result = prune(
            "folder_path",
            asset_types=["IMAGE"],
            silent=True,
        )
        assert result == {"deleted": [], "failed": [], "skipped": []}
        mock_delete.assert_not_called()


class TestPruneFailures:
    def test_delete_failure_recorded(
        self, mock_get_asset, mock_delete, mock_tqdm
    ):
        asset_path = "projects/p/assets/img"
        mock_get_asset.return_value = {"type": "IMAGE"}
        mock_delete.side_effect = EEException("permission denied")

        result = prune(asset=asset_path, silent=True)

        assert result["deleted"] == []
        assert result["failed"] == [
            {"asset": asset_path, "error": "permission denied"}
        ]
        assert result["skipped"] == []


class TestPruneProjectRoot:
    def test_never_deletes_root(
        self, mock_list_assets, mock_delete, mock_tqdm, mocker
    ):
        project_root = "projects/my-project/assets"
        children = [
            {"name": f"{project_root}/folder1/image1", "type": "IMAGE"},
            {"name": f"{project_root}/folder1", "type": "FOLDER"},
            {"name": f"{project_root}/image2", "type": "IMAGE"},
        ]
        mock_list_assets.return_value = children
        mocker.patch("gee_toolbox.assets.assets.ee.data.getAsset")

        result = prune(
            asset=project_root,
            recursive=True,
            expand_image_collections=True,
            inclusive=True,
            silent=True,
            dry_run=False,
        )

        deleted = result["deleted"]
        assert project_root not in deleted
        assert set(deleted) == {c["name"] for c in children}
        assert mock_delete.call_count == len(children)

    def test_dry_run_excludes_root(self, mock_list_assets, mock_delete):
        project_root = "projects/my-project/assets"
        children = [
            {"name": f"{project_root}/image1", "type": "IMAGE"},
            {"name": f"{project_root}/folder1", "type": "FOLDER"},
        ]
        mock_list_assets.return_value = children

        result = prune(
            asset=project_root,
            recursive=True,
            expand_image_collections=True,
            inclusive=True,
            silent=True,
            dry_run=True,
        )

        assert mock_list_assets.call_args.kwargs["inclusive"] is False
        assert project_root not in result["skipped"]
        assert set(result["skipped"]) == {c["name"] for c in children}
        mock_delete.assert_not_called()
