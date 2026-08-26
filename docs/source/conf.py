"""Configuration file for the Sphinx documentation builder."""

from importlib.metadata import version as get_version

from sphinx.application import Sphinx

project = "gee-toolbox"
copyright = "2026, Erick G"
author = "Erick G"
release = get_version("gee-toolbox")  # full version, e.g. 0.2.1
version = ".".join(release.split(".")[:2])  # short X.Y (optional)


extensions = [
    "myst_parser",
    "autoapi.extension",
    "sphinx.ext.napoleon",  # Google-style docstring
    "sphinx_design",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

templates_path = ["_templates"]
exclude_patterns = []

language = "en"

html_static_path = ["_static"]
html_theme = "furo"
html_theme_options = {
    "source_repository": "https://github.com/silversky54/gee-toolbox",
    "source_branch": "main",
    "source_directory": "docs/source/",
}

# AutoAPI — paths are relative to docs/source/
autoapi_dirs = ["../../src"]
autoapi_options = [
    "members",
    "undoc-members",
    "imported-members",  # show public API re-exported in package __init__.py
    "show-inheritance",
    "show-module-summary",
]
# Manual toctree entry in index.md; avoid a duplicate sidebar link
autoapi_add_toctree_entry = False
autoapi_keep_files = False

napoleon_google_docstring = True
napoleon_numpy_docstring = False

myst_enable_extensions = [
    "colon_fence",
    "deflist",
]


def _skip_submodules(app, what, name, obj, skip, options):
    """Document packages (via __init__.py) only; hide implementation modules.

    Public API is re-exported from package __init__ files. Without this, AutoAPI
    also builds pages for modules like gee_toolbox.assets.assets.
    See: https://sphinx-autoapi.readthedocs.io/en/stable/how_to.html
    """
    if what == "module":
        return True
    return skip


def setup(app: Sphinx) -> None:
    """Register Sphinx / AutoAPI hooks."""
    app.connect("autoapi-skip-member", _skip_submodules)
