"""Smoke test: proves the joblens package is installed, not just present on disk."""

from importlib.metadata import version

import joblens


def test_package_is_installed():
    # With the src/ layout, this metadata only exists if uv installed the package.
    assert version("joblens") == joblens.__version__
