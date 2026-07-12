"""Sphinx configuration for genro-sqlmigration documentation."""

import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path("..").resolve() / "src"))

# Project information
project = "genro-sqlmigration"
copyright = "2025-2026, Softwell S.r.l."
author = "Softwell S.r.l."
try:
    release = _pkg_version("genro-sqlmigration")
except PackageNotFoundError:
    release = "0.1.0"

# Extensions
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "myst_parser",
    "sphinxcontrib.mermaid",
]

# Templates
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# HTML output
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "collapse_navigation": False,  # keep section tree visible on every page
    "navigation_depth": 3,
}

# Intersphinx
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
