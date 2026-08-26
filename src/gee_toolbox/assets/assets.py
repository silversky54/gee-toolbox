"""Helper functions for Google Earth Engine Assets Management."""

import logging
from pathlib import Path

import ee
import ee.data
from ee.ee_exception import EEException
from tqdm.auto import tqdm

logger = logging.getLogger(__name__)

ASSET_TYPES = ["IMAGE", "TABLE", "FOLDER", "IMAGE_COLLECTION"]
"""Valid Earth Engine asset type names for list/prune filters."""

CONTAINER_ASSET_TYPES = ["FOLDER", "IMAGE_COLLECTION"]
"""Asset types that can contain other assets."""

DEFAULT_PAGINATION_SIZE = 1000
"""Default page size when listing assets via the Earth Engine client."""


def _is_project_root(asset: str | Path) -> bool:
    """Return True if `asset` is a GEE project assets root.

    Project roots have the form `projects/<project-id>/assets`. `ee.data.getAsset`
    rejects them, and they must never be deleted.
    """
    asset = Path(asset).as_posix()
    parts = asset.rstrip("/").split("/")
    return len(parts) == 3 and parts[0] == "projects" and parts[2] == "assets"


def _request_del_confirmation() -> bool:
    """Request Y/N confirmation from user. No input will return False."""
    confirmation_loop = True
    delete_confirmation = None
    while confirmation_loop:
        delete_confirmation = input(
            "Are you sure you want to delete this asset? (Y/N)"
        ).lower()
        if delete_confirmation in ["y", "n", ""]:
            confirmation_loop = False

    return delete_confirmation == "y"


def _make_del_warning(asset: str, objects_list: list, *, dry_run: bool = False) -> str:
    """Make a warning message for the deletion of assets."""
    image_collections = sum(1 for t in objects_list if t == "IMAGE_COLLECTION")
    images = sum(1 for t in objects_list if t == "IMAGE")
    tables = sum(1 for t in objects_list if t == "TABLE")
    folders = sum(1 for t in objects_list if t == "FOLDER")
    action = "would delete" if dry_run else "are about to delete"
    warn_text = (
        "******************************************************\n"
        "WARNING\n"
        "******************************************************\n"
        f"You {action} the following assets:\n"
        f"-Images: {images}\n"
        f"-Image Collections: {image_collections}\n"
        f"-Tables: {tables}\n"
        f"-Folders: {folders}\n"
        f"Target: {asset}\n"
    )
    return warn_text


def _check_asset_types(asset_types: str | list[str] | None) -> list[str]:
    """Check if the asset types are valid."""
    # verify allowed asset types
    if not asset_types:
        asset_types = list(ASSET_TYPES)
    elif isinstance(asset_types, str):
        asset_types = [asset_types]

    # Convert all asset types to uppercase
    asset_types = [asset_type.upper() for asset_type in asset_types]

    # if asset types not in valid types, raise an error
    if not all(asset in ASSET_TYPES for asset in asset_types):
        raise ValueError(f"Invalid asset type(s). Must be one of {ASSET_TYPES}")

    return asset_types


def _get_asset_names(asset_list: list) -> list:
    """Return a list of asset names from the given asset list.

    Args:
        asset_list: A list of assets dictionaries with keys "name" and "type".

    Returns:
        List: A list of asset names.

    """
    return [asset["name"] for asset in asset_list if "name" in asset]


def _get_asset_types(asset_list: list) -> list:
    """Return a list of asset types from the given asset list.

    Args:
        asset_list: A list of assets dictionaries with keys "name" and "type".

    Returns:
        List: A list of asset types.

    """
    return [asset["type"] for asset in asset_list if "type" in asset]


def _fetch_child_assets(
    parent: str,
    page_size: int | None = DEFAULT_PAGINATION_SIZE,
) -> list[dict]:
    """List direct children of a GEE folder or image collection.

    The Earth Engine REST API paginates ``listAssets`` responses (``pageSize`` /
    ``pageToken`` / ``nextPageToken``). The Python client auto-fetches all pages
    when ``pageSize`` is omitted; when ``pageSize`` is set it returns a single
    page. This helper always collects every page so large folders are complete.

    Args:
        parent: Path to the folder or image collection.
        page_size: Page size sent to the API. ``None`` lets the client auto-page
            in one call. Defaults to ``DEFAULT_PAGINATION_SIZE`` (explicit paging).

    Returns:
        List of asset dicts from the API (at least ``name`` and ``type``).

    """
    if page_size is None:
        response = ee.data.listAssets({"parent": parent})
        return response.get("assets", [])

    assets: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {"parent": parent, "pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token

        response = ee.data.listAssets(params)
        assets.extend(response.get("assets", []))
        next_token = response.get("nextPageToken")
        page_token = next_token if isinstance(next_token, str) else None
        if not page_token:
            break

    return assets


# Creating alternative function to list assets to include filtering by asset type and
# inclusion/exclusion of parent folder
def list_assets(
    parent: str,
    asset_types: str | list[str] | None = None,
    recursive: bool = False,
    inclusive: bool = False,
    expand_image_collections: bool = False,
    image_collections_exclusively: bool = False,
    names_only: bool = False,
    page_size: int | None = DEFAULT_PAGINATION_SIZE,
) -> list:
    """List assets in a container (folder or Image Collection) in GEE.

    Asset types can be any of ASSET_TYPES. If empty/None, all asset types are listed.
    An empty container returns an empty list (does not raise).

    Args:
        parent: path to the parent folder of the assets
        asset_types: asset types to list. If empty/None, all asset types are listed
        recursive: Recursively search for assets in sub-folders
        inclusive: Include the parent folder in the list
        expand_image_collections: Expand image collections to include images
        image_collections_exclusively: Include images in image collections exclusively
            (omit standalone IMAGE assets outside image collections)
        names_only: Return only the names of the assets
        page_size: Page size. Use 'None' to let the Earth Engine client auto-paginate
            without an explicit page size.

    Returns:
        list: List of asset dictionaries with keys "name" and "type", or just names
        if names_only=True.

    """
    # reference: https://github.com/spatialthoughts/projects/blob/master/ee-python/list_all_assets.py

    asset_types = _check_asset_types(asset_types)

    # Cannot use getAsset on Project roots; treat as a folder container.
    if _is_project_root(parent):
        parent_type = "FOLDER"
        if inclusive:
            logger.info(
                "Parent %s is a project assets root; omitting it from the list "
                "(roots are not assets)",
                parent,
            )
            inclusive = False
    else:
        # If Asset is not a folder or image collection raise error
        try:
            parent_type = ee.data.getAsset(parent)["type"]
            if parent_type not in CONTAINER_ASSET_TYPES:
                raise ValueError("Path provided is not a Folder or Image Collection")
        except (ValueError, EEException) as e:
            logger.error("Error reading parent asset: %s", e)
            raise
        except Exception as e:
            logger.error(e)
            raise

    # If parent is an image collection, expand_image_collections is forced to True
    if parent_type == "IMAGE_COLLECTION" and not expand_image_collections:
        msg = (
            "Parent container is an ImageCollection but expand_image_collections was "
            "set to 'False'. expand_image_collections will be forced to 'True' to "
            "avoid returning an empty list."
        )
        logger.warning(msg)
        expand_image_collections = True

    child_assets = _fetch_child_assets(parent, page_size=page_size)

    asset_list = []

    # if Inclusive is True add container info to the list
    if inclusive:
        asset_list.append({"name": parent, "type": parent_type})

    # Iterate over child assets.
    for child_asset in child_assets:
        child_type = child_asset["type"]
        child_name = child_asset["name"]

        # Skip standalone images when only IC contents are requested.
        # Images whose parent is an IMAGE_COLLECTION are kept.
        include_child = (
            not image_collections_exclusively
            or child_type != "IMAGE"
            or parent_type == "IMAGE_COLLECTION"
        )
        if include_child:
            asset_list.append({"name": child_name, "type": child_type})

        # Recursively call the function to get child assets
        if recursive and child_type == "FOLDER":
            asset_list.extend(
                list_assets(
                    child_name,
                    asset_types=None,  # Bring all, filter at the end
                    recursive=True,
                    inclusive=False,  # Parent already included above when requested
                    expand_image_collections=expand_image_collections,
                    image_collections_exclusively=image_collections_exclusively,
                    names_only=False,
                    page_size=page_size,
                )
            )
        if expand_image_collections and child_type == "IMAGE_COLLECTION":
            # Image collections only contain images; recursion into folders is N/A
            asset_list.extend(
                list_assets(
                    child_name,
                    asset_types=None,  # Bring all, filter at the end
                    recursive=False,
                    inclusive=False,
                    expand_image_collections=True,
                    # Keep IC images even when exclusive mode is on for folders above
                    image_collections_exclusively=False,
                    names_only=False,
                    page_size=page_size,
                )
            )

    # Filter assets not in asset_types
    asset_list = [asset for asset in asset_list if asset["type"] in asset_types]

    if names_only:
        asset_list = _get_asset_names(asset_list)

    return asset_list


def prune(
    asset: str,
    asset_types: str | list[str] | None = None,
    recursive: bool = False,
    expand_image_collections: bool = False,
    image_collections_exclusively: bool = False,
    inclusive: bool = True,
    silent: bool = False,
    dry_run: bool = False,
) -> dict[str, list]:
    """Delete Google Earth Engine assets in Google Projects.

    `Prune` can delete multiple assets if the specified asset is a older or
    ImageCollection. Delete recursively in sub-folders by setting recursive=True.
    Specific asset types can be targeted using the asset_types argument. If asset_types
    is empty, all asset types will be considered. See ASSET_TYPES for valid types.
    Folders and ImageCollections will be excluded if not included in asset_types even
    if inclusive=True.
    Deleting ImageCollections automatically includes their images (GEE cannot delete
    a non-empty container); expand_image_collections=True is required in that case.
    Images in ImageCollections can be targeted exclusively by setting
    image_collections_exclusively=True, which omits standalone IMAGE assets outside
    ImageCollections.
    When `asset` is a project assets root (`projects/<id>/assets`), all matching
    children can be deleted but the root itself is never deleted (inclusive is forced
    to False).

    Args:
        asset: The path to the asset.
        asset_types: One or more asset types to delete. See ASSET_TYPES for valid asset
            types. None or [] deletes all asset types.
        recursive: Include sub-folders recursively. Required if deleting folders.
        expand_image_collections: Include images in ImageCollections. Required if
            deleting folders or ImageCollections.
        image_collections_exclusively: Only assets within ImageCollections are included
            (standalone images are omitted). Forced to True when deleting Collections
            without IMAGE in asset_types.
        inclusive: Include the top asset if asset is a folder or ImageCollection.
            Ignored if Folder or ImageCollection is not included in asset_types.
        silent: Skip the confirmation prompt. Use with caution, will delete all assets
            without requesting confirmation.
        dry_run: List all assets to delete without deleting them.

    Raises:
        ValueError: If `asset_types` includes a container but omits all other required
            asset types (IMAGE, TABLE, etc).
        ValueError: If deleting a container but recursive=False or
            expand_image_collections=False.

    """
    _asset = asset
    results: dict[str, list] = {"deleted": [], "failed": [], "skipped": []}

    # Project assets roots (projects/<id>/assets) cannot be deleted.
    # Treat as a folder container and never include the root in the deletion list.
    if _is_project_root(_asset):
        _asset_type = "FOLDER"
        _is_container = True
        if inclusive:
            logger.info(
                "Asset %s is a project assets root; forcing inclusive=False so "
                "the root is not deleted",
                _asset,
            )
            inclusive = False
    else:
        try:
            _asset_type = ee.data.getAsset(_asset)["type"]
            _is_container = _asset_type in CONTAINER_ASSET_TYPES
        except (EEException, Exception) as e:
            logger.error("Error reading asset: %s", e)
            raise

    asset_types = _check_asset_types(asset_types)
    logger.debug(
        "prune(%s) type=%s asset_types=%s recursive=%s expand_ic=%s "
        "ic_exclusively=%s inclusive=%s dry_run=%s silent=%s",
        _asset,
        _asset_type,
        asset_types,
        recursive,
        expand_image_collections,
        image_collections_exclusively,
        inclusive,
        dry_run,
        silent,
    )

    # if asset_types includes FOLDER, asset_types should also include all types
    if (
        _asset_type == "FOLDER"
        and "FOLDER" in asset_types
        and not all(allowed_type in asset_types for allowed_type in ASSET_TYPES)
    ):
        raise ValueError(
            "Can't delete 'FOLDER' types without including all other required asset "
            "types. Use asset_types=[] to delete folders and all their contents, and "
            "set 'asset' to the desired root folder to avoid collateral damage "
        )

    # IF deleting folders, recursive and expand_image_collections need to be True
    if (
        "FOLDER" in asset_types
        and _asset_type == "FOLDER"
        and (not recursive or not expand_image_collections)
    ):
        raise ValueError(
            "Deleting folders requires recursive=True and expand_image_collections=True"
        )

    # If Deleting image collections, expand_image_collections needs to be True
    if (
        _asset_type in CONTAINER_ASSET_TYPES
        and "IMAGE_COLLECTION" in asset_types
        and not expand_image_collections
    ):
        raise ValueError(
            "Deleting an ImageCollection requires expand_image_collections=True"
        )

    # GEE cannot delete a non-empty ImageCollection: include its images automatically.
    # Prefer IC-only images so standalone IMAGE assets are not collateral damage.
    if "IMAGE_COLLECTION" in asset_types and "IMAGE" not in asset_types:
        logger.warning(
            "IMAGE_COLLECTION deletion requires removing nested images first; "
            "adding 'IMAGE' to asset_types with image_collections_exclusively=True"
        )
        image_collections_exclusively = True
        asset_types.append("IMAGE")

    if _is_container:
        asset_list = list_assets(
            parent=_asset,
            asset_types=asset_types,
            recursive=recursive,
            inclusive=inclusive,
            expand_image_collections=expand_image_collections,
            image_collections_exclusively=image_collections_exclusively,
        )
    elif _asset_type in asset_types:
        asset_list = [{"name": _asset, "type": _asset_type}]
    else:
        logger.info(
            "Asset %s type %s is not in asset_types %s; nothing to delete",
            _asset,
            _asset_type,
            asset_types,
        )
        asset_list = []

    if not asset_list:
        logger.info("No matching assets to delete under %s", _asset)
        return results

    warning = _make_del_warning(_asset, _get_asset_types(asset_list), dry_run=dry_run)
    # Banner stays on stdout for interactive/notebook visibility.
    print(warning, flush=True)
    logger.info(
        "%s %d matching asset(s) under %s",
        "Would delete" if dry_run else "About to delete",
        len(asset_list),
        _asset,
    )

    # Split and sort per level of hierarchy.
    # Recursive deleting will fail If not deleted in reverse order
    assets_ordered: dict[int, list[Path]] = {}
    for _target_asset in asset_list:
        target_path = Path(_target_asset["name"])
        lvl = len(target_path.parts)
        assets_ordered.setdefault(lvl, []).append(target_path)
    assets_ordered = dict(sorted(assets_ordered.items(), reverse=True))

    # End if Dry Run
    if dry_run:
        results["skipped"] = _get_asset_names(asset_list)
        logger.info(
            "Dry run: %d asset(s) would be deleted from %s", len(asset_list), _asset
        )
        return results

    # Warn user and ask for confirmation
    if silent:
        delete_confirmation = True
        logger.debug("silent=True; skipping confirmation prompt")
    else:
        delete_confirmation = _request_del_confirmation()

    if delete_confirmation:

        def _delete(target: Path) -> None:
            asset_id = target.as_posix()
            try:
                ee.data.deleteAsset(asset_id)
                results["deleted"].append(asset_id)
                logger.debug("Deleted %s", asset_id)
            except EEException as e:
                results["failed"].append({"asset": asset_id, "error": str(e)})
                logger.error("Failed to delete %s: %s", asset_id, e)

        delete_order = [
            target for lvl in assets_ordered for target in assets_ordered[lvl]
        ]
        logger.info("Deleting %d item(s) from %s", len(delete_order), _asset)

        for target in tqdm(delete_order):
            _delete(target)

        logger.info(
            "Deleted %d item(s), %d failed under %s",
            len(results["deleted"]),
            len(results["failed"]),
            _asset,
        )
    else:
        results["skipped"] = _get_asset_names(asset_list)
        logger.info(
            "Deletion cancelled; %d asset(s) skipped under %s", len(asset_list), _asset
        )
    return results


def check_asset_exists(asset: str, asset_type: str | None = None) -> bool:
    """Verify if an asset exists in GEE Assets.

    If asset_type is provided, returns True only if the asset is of the specified type.
    asset_type can be any of ASSET_TYPES.

    Args:
        asset: path to the asset in GEE
        asset_type: type of asset expected.

    Returns:
        Returns True if asset is found, False if it isn't

    Raises:
        TypeError: if asset_type is not a string
        ValueError: if asset_type is not a valid asset type

    """
    if asset_type and asset_type.upper() not in ASSET_TYPES:
        raise ValueError(f"Invalid asset type: {asset_type}")

    try:
        asset_info = ee.data.getAsset(asset)
        if asset_info:
            if asset_type:
                return asset_info["type"].upper() == asset_type.upper()
            else:
                return True
        else:
            return False

    except EEException:
        # TODO: Separate not found error from other errors
        return False


def check_container_exists(asset: str | Path) -> bool:
    """Check if a folder or ImageCollection exists in Google Earth Engine Assets.

    Returns False if is not a valid container (folder or ImageCollection).

    Args:
        asset (str): The path of the asset.

    Returns:
        bool: True if exists and is a container, False otherwise.

    """
    if isinstance(asset, Path):
        asset = asset.as_posix()
    try:
        asset_info = ee.data.getAsset(asset)
        if asset_info:
            return asset_info["type"] in CONTAINER_ASSET_TYPES
        else:
            return False
    except EEException:
        # TODO: Separate not found error from other errors
        return False
