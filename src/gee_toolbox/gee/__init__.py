import warnings

from .assets import (
    ALLOWED_ASSET_TYPES,
    get_asset_names,
    get_asset_types,
    list_assets,
    prune,
)

warnings.warn(
    "The 'gee' module is deprecated and will be removed in version 2.0. "
    "Please migrate to 'gee_toolbox.assets' instead. Note that some functions "
    "may have been renamed or moved to the new module.",
    category=DeprecationWarning,
    stacklevel=2,
)


__all__ = [
    "ALLOWED_ASSET_TYPES",
    "get_asset_names",
    "get_asset_types",
    "list_assets",
    "prune",
]
