"""Packaging: the installed distribution contains the Zoho packages and imports outside the checkout.

The install test needs PyPI access (build isolation + httpx), so it only runs when opted in
with ZOHO_RUN_PACKAGING=1 (and skips offline). The pyproject listing test always runs.
"""
import os
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_lists_zoho_packages_and_respx():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = cfg["tool"]["setuptools"]["packages"]
    assert "integrations" in packages and "integrations.zoho" in packages
    assert any(d.startswith("respx") for d in cfg["project"]["optional-dependencies"]["dev"])


def _pypi_reachable() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.packaging
@pytest.mark.skipif(os.environ.get("ZOHO_RUN_PACKAGING") != "1",
                    reason="opt-in: set ZOHO_RUN_PACKAGING=1 (builds a venv, needs PyPI)")
def test_install_into_fresh_venv_and_import_from_outside(tmp_path):
    if not _pypi_reachable():
        pytest.skip("PyPI not reachable (offline)")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(
        ".git", ".venv", "venv", "__pycache__", "*.egg-info", "build", ".pytest_cache", ".env*"))
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    pip = [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    subprocess.run([*pip, "httpx", "mcp>=1.2.0,<2"], check=True, timeout=600)
    subprocess.run([*pip, "--no-deps", str(src)], check=True, timeout=600)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    code = ("import odin_mcp.zoho_tools as t, integrations.zoho.client as c; "
            "print(t.__file__); print(c.__file__)")
    proc = subprocess.run([str(py), "-I", "-c", code], cwd=outside, check=False,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.split()
    assert all("site-packages" in p and str(ROOT) not in p for p in out), out
