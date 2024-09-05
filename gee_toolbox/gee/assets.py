import ee
from geetools.Asset import Asset
import logging
from typing import List, Dict, Any


def _request_del_confirmation() -> bool:
    # request Y/N confirmation from user. No input will return False
    confirmation_loop = True
    while confirmation_loop:
        delete_confirmation = input(
            "Are you sure you want to delete this asset? (Y/N)"
        ).lower()
        if delete_confirmation in ["y", "n", ""]:
            confirmation_loop = False

    return delete_confirmation == "y"


def _make_del_warning(asset: str, objects_list: list) -> str:
    # count items in asset_types that are equal to type 'IMAGE_COLLECTION'
    image_collections = len(
        [type for type in objects_list if type == "IMAGE_COLLECTION"]
    )
    images = len([type for type in objects_list if type == "IMAGE"])
    tables = len([type for type in objects_list if type == "TABLE"])
    folders = len([type for type in objects_list if type == "FOLDER"])
    warn_text = (
        "******************************************************\n"
        "WARNING\n"
        "******************************************************\n"
        "You are about to delete the following assets:\n"
        f"-Images: {images}\n"
        f"-Image Collections: {image_collections}\n"
        f"-Tables: {tables}\n"
        f"-Folders: {folders}\n"
        f"Target: {asset}\n"
    )
    return warn_text


ALLOWED_ASSET_TYPES = ["IMAGE", "TABLE", "FOLDER", "IMAGE_COLLECTION"]


def _check_asset_types(asset_types: str | List[str]) -> List[str]:
    # verify allowed asset types
    if not asset_types:
        asset_types = ALLOWED_ASSET_TYPES
    if type(asset_types) == str:
        asset_types = [asset_types]

    # Convert all asset types to uppercase
    asset_types = [asset_type.upper() for asset_type in asset_types]

    # if asset types not in valid types, raise an error
    if not all([asset in ALLOWED_ASSET_TYPES for asset in asset_types]):
        raise ValueError(f"Invalid asset type(s). Must be one of {ALLOWED_ASSET_TYPES}")

    return asset_types


def get_asset_names(asset_list: list) -> List:
    return [asset["name"] for asset in asset_list]


def get_asset_types(asset_list: list) -> List:
    return [asset["type"] for asset in asset_list]


# Creating alternative function to list assets to include filtering by asset type and inclusion/exclusion of parent folder
def list_assets(
    parent: str,
    asset_types: str | list = [],
    recursive: bool = False,
    inclusive: bool = False,
    expand_image_collections: bool = False,
    image_collections_exclusively: bool = False,
) -> List:
    """
    lists assets from an assets folder or Image Collection in GEE. User can specify what type of assets to list.

    Args:
        parent: path to the parent folder of the assets
        asset_type: asset types to list. ['IMAGE', 'TABLE', 'FOLDER', 'IMAGE_COLLECTION']. If empty, all asset types are listed
        recursive: Recursively search for assets in sub-folders
        inclusive: Include the parent folder in the list

    reference: https://github.com/spatialthoughts/projects/blob/master/ee-python/list_all_assets.py
    """

    # TODO - IF parent is image collection, should expand_image_collections be forced to true?
    # TODO - Add handler for when results are paginated
    # TODO - IF list is empty should this fail??

    asset_types = _check_asset_types(asset_types)

    # If Asset is not a folder or image collection raise error
    try:
        _parent = Asset(parent)
        parent_type = _parent.type
        if parent_type not in ["FOLDER", "IMAGE_COLLECTION"]:
            raise ValueError("Path provided is not a Folder or Image Collection")
    except (ValueError, ee.EEException) as e:
        logging.error(e)
        raise e

    # If parent is an image collection, expand_image_collections is forced to True
    if parent_type == "IMAGE_COLLECTION":
        expand_image_collections = True

    # List assets in the parent folder
    try:
        child_assets = ee.data.listAssets({"parent": _parent.as_posix()})["assets"]
    except ee.EEException as e:
        logging.warning(e)
        raise e

    asset_list = []

    # if Inclusive is True add container info to the list
    if inclusive:
        asset_list.append({"name": _parent.as_posix(), "type": parent_type})

    # Iterate over child assets.
    for child_asset in child_assets:
        # if not image_collections_exclusively, include everything
        if (
            not image_collections_exclusively
            or child_asset["type"] != "IMAGE"
            or parent_type == "IMAGE_COLLECTION"
        ):
            asset_list.append(
                {"name": child_asset["name"], "type": child_asset["type"]}
            )

        # Recursively call the function to get child assets
        if recursive and child_asset["type"] == "FOLDER":
            asset_list.extend(
                list_assets(
                    child_asset["name"],
                    asset_types=[],  # Bring all, filter at the end
                    recursive=True,  # Implicit from IF
                    inclusive=False,  # False because parents are included above
                    expand_image_collections=expand_image_collections,
                    image_collections_exclusively=image_collections_exclusively,
                )
            )
        if expand_image_collections and child_asset["type"] == "IMAGE_COLLECTION":
            # Recursively call the function to get child assets
            asset_list.extend(
                list_assets(
                    child_asset["name"],
                    asset_types=[],  # Bring all, filter at the end
                    recursive=True,  # Indiferente, image collections are not recursive
                    inclusive=False,  # False because parents are included above
                    expand_image_collections=True,  # Implicit from IF
                    image_collections_exclusively=False,
                )
            )

    # Filter assets not in asset_types
    asset_list = [asset for asset in asset_list if asset["type"] in asset_types]

    return asset_list


def prune(
    asset: str,
    asset_types: str | List[str] = [],
    recursive: bool = False,
    expand_image_collections: bool = False,
    inclusive: bool = True,
    silent: bool = False,
    dry_run: bool = False,
) -> Dict[str, List[str]]:
    """
    Deletes Google Earth Engine assets in google projects.

    Prune function can delete multiple assets if the specified asset is a folder or image collection. Delete recursively
    in sub-folders by setting recursive=True. Specific asset types can be targeted using the asset_types argument. If
    asset_types is empty, all asset types will be considered.
    Folders and Image Collections will be excluded if not included in asset_types even if inclusive=True.
    Deleting Image Collections will automatically include it's images in which case expand_image_collections=True is required.
    Images in Image Collections can be targeted exclusively by setting image_collections_exclusively=True, this will
    omit images that are not in an Image Collection.

    Args:
        asset (str): The path to the asset.
        asset_types (str | List[str], optional): One or more asset types to delete. Defaults to []. Valid asset
            types are 'IMAGE', 'TABLE', 'FOLDER', 'IMAGE_COLLECTION'. An empty list [] deletes all asset types.
        recursive (bool, optional): Whether to include sub-folders recursively. Defaults to False.
            Required if deleting folders.
        expand_image_collections (bool, optional): Whether to include images in image collections. Defaults to False.
            Required if deleting folders or image collections.
        inclusive (bool, optional): Whether to include the top asset if asset is a folder or image collection.
            Defaults to True. Ignored if Folder or Image Collection is not included in asset_types.
        silent (bool, optional): Whether to skip the confirmation prompt. Defaults to False.
            Use with caution, will delete all assets without requesting confirmation.
        dry_run (bool, optional): List all assets to delete without deleting them. Defaults to False.
    Returns:
        Dict: A dictionary containing the results of the deletion operation. The dictionary has the following keys:
            - "deleted": List of assets successfully deleted.
            - "failed": list of assets that failed to delete.
            - "skipped": List of assets that were skipped from deleting.
    Raises:
        ValueError: If asset_types includes 'FOLDER' but omits any other required asset types.
        ValueError: If deleting a folder but recursive=False or expand_image_collections=False.
        ValueError: If deleting an image collection but expand_image_collections=False.
    """

    _asset = Asset(asset)
    results = {"deleted": [], "failed": [], "skipped": []}

    _asset.exists(raised=True)
    asset_types = _check_asset_types(asset_types)

    # if asset_types includes FOLDER, asset_types should also include all other asset types or raise error
    if any([asset_type == "FOLDER" for asset_type in asset_types]):
        if not all(
            [allowed_type in asset_types for allowed_type in ALLOWED_ASSET_TYPES]
        ):
            raise ValueError(
                (
                    "asset_types includes 'FOLDER' but omits other required asset types. "
                    "Prefer asset_types=[] to delete folders and all their contents."
                )
            )

    # IF deleting folders, recursive and expand_image_collections need to be True
    if (
        "FOLDER" in asset_types
        and not _asset.is_image_collection()
        and (not recursive or not expand_image_collections)
    ):
        raise ValueError(
            "Deleting a folder requires recursive=True and expand_image_collections=True"
        )

    # If Deleting image collections, expand_image_collections needs to be True
    if "IMAGE_COLLECTION" in asset_types and not expand_image_collections:
        raise ValueError(
            "Deleting an image collection requires expand_image_collections=True"
        )

    # if deleting image collections  and not images (explicitly)
    # add 'IMAGE' to asset_types and set image_collection_exclusively=True
    image_collections_exclusively = False
    if "IMAGE_COLLECTION" in asset_types and not "IMAGE" in asset_types:
        image_collections_exclusively = True
        asset_types.append("IMAGE")

    is_container = (
        _asset.is_project() or _asset.is_folder() or _asset.is_image_collection()
    )

    # list objects (images, tables, folders, imageCollections, etc) in folder and sub folders
    if is_container:
        asset_list = list_assets(
            parent=_asset.as_posix(),
            asset_types=asset_types,
            recursive=recursive,
            inclusive=inclusive,
            expand_image_collections=expand_image_collections,
            image_collections_exclusively=image_collections_exclusively,
        )

        # Split and sort per level of hierarchy. Recursive deleting will fail If not deleted in reverse order
        assets_ordered: dict = {}
        for _target_asset in asset_list:
            _target_asset = Asset(_target_asset["name"])
            lvl = len(_target_asset.parts)
            assets_ordered.setdefault(lvl, [])
            assets_ordered[lvl].append(_target_asset)
        assets_ordered = dict(sorted(assets_ordered.items(), reverse=True))

    else:
        asset_list = [{"name": _asset.as_posix(), "type": _asset.type}]

    print(_make_del_warning(_asset.as_posix(), get_asset_types(asset_list)))

    # End if Dry Run
    if dry_run:
        results["skipped"] = get_asset_names(asset_list)
        print("Dry run, no items will be deleted")
        return results

    # Warn user and ask for confirmation
    if silent:
        delete_confirmation = True
    else:
        delete_confirmation = _request_del_confirmation()

    # Proceed to delete
    # Not using Asset.delete() method. Need to be able to delete specific asset types only
    if delete_confirmation:

        def _delete(asset: Asset) -> None:
            try:
                ee.data.deleteAsset(str(asset))
                results["deleted"].append(str(asset))
            except Exception as e:
                results["failed"].append(str(asset))

        print(f"Deleting {len(asset_list)} items from {_asset.as_posix()}")

        # delete all items starting from the more nested ones
        for lvl in assets_ordered:
            [_delete(asset) for asset in assets_ordered[lvl]]

        print(
            f"Deleted {len(results['deleted'])} items, {len(results['failed'])} items failed to delete"
        )
    else:
        results["skipped"] = get_asset_names(asset_list)
        print(f"No items deleted from {_asset.as_posix()}")
    return results


if __name__ == "__main__":
    pass
