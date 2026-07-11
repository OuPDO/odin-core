"""Tests fuer scripts.reindex_repos -- git und Ingestion gemockt."""
import base64
import subprocess
from unittest.mock import patch

import pytest

import scripts.reindex_repos as rr
from scripts.ingest import IngestResult

_ING3 = IngestResult(embedded=3, skipped_unchanged=0, errors=0)


def test_reindex_builds_config_identity_and_upserts(tmp_path):
    """reindex builds one row per repo; identity = config owner/repo (authoritative)."""
    repos = [
        {"repo": "OuPDO/ODIN", "org": "do", "branch": "main"},
        {"repo": "OuPDO/OMNIPULSE", "org": "om", "branch": "main"},
    ]

    with patch.object(rr, "get_indexed_repos", return_value=repos), \
         patch.object(rr, "_clone_or_pull", return_value=str(tmp_path)), \
         patch.object(rr, "_stack", return_value=None), \
         patch.object(rr, "_purpose", return_value=None), \
         patch.object(rr, "upsert_project") as mock_upsert, \
         patch.object(rr, "ingest_embeddings", return_value=_ING3) as mock_ingest:
        result = rr.reindex(str(tmp_path))

    assert result["repos_ok"] == 2
    assert result["repos_failed"] == 0
    assert result["chunks"] == 6  # 2 repos x 3 embedded

    for i, entry in enumerate(repos):
        row = mock_upsert.call_args_list[i].args[0]
        assert row["git_remote"] == entry["repo"]
        assert row["path"] == entry["repo"]
        assert row["org"] == entry["org"]
        # ingest_embeddings called with exactly [row]
        assert mock_ingest.call_args_list[i].args[0] == [row]


def test_reindex_skips_failed_repo(tmp_path):
    """Clone failure increments repos_failed; succeeding repo still counted."""
    repos = [
        {"repo": "OuPDO/a", "org": "do", "branch": "main"},
        {"repo": "OuPDO/b", "org": "om", "branch": "main"},
    ]

    with patch.object(rr, "get_indexed_repos", return_value=repos), \
         patch.object(rr, "_clone_or_pull", side_effect=[str(tmp_path), RuntimeError("boom")]), \
         patch.object(rr, "_stack", return_value=None), \
         patch.object(rr, "_purpose", return_value=None), \
         patch.object(rr, "upsert_project"), \
         patch.object(rr, "ingest_embeddings", return_value=_ING3):
        result = rr.reindex(str(tmp_path))

    assert result["repos_ok"] == 1
    assert result["repos_failed"] == 1


def test_clone_failure_does_not_leak_token():
    """CalledProcessError stderr is scrubbed; RuntimeError must not contain the token."""
    token = "ghp_SECRET"
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    cmd = [
        "git", "-c", f"http.extraHeader=Authorization: Basic {b64}",
        "clone", "--depth", "1", "-b", "main",
        "https://github.com/OuPDO/x.git", "/dest",
    ]
    exc = subprocess.CalledProcessError(
        128, cmd,
        stderr=f"fatal: could not read Password for 'https://x-access-token:{token}@github.com'",
    )

    with patch("subprocess.run", side_effect=exc):
        with pytest.raises(RuntimeError) as ei:
            rr._clone_or_pull("OuPDO/x", "main", "/dest", token)

    assert token not in str(ei.value), "token must not appear in RuntimeError message"
    assert "git failed" in str(ei.value)
