from unittest.mock import patch

from scripts.ingest import _canonical_remote, scan_projects

def test_canonical_remote():
    # tokenized https -> strips creds, drops .git
    assert _canonical_remote("https://x-access-token:ghp_X@github.com/OuPDO/ODIN.git") == "OuPDO/ODIN"
    # plain https
    assert _canonical_remote("https://github.com/OuPDO/ODIN.git") == "OuPDO/ODIN"
    # ssh
    assert _canonical_remote("git@github.com:OuPDO/ODIN.git") == "OuPDO/ODIN"
    # None passthrough
    assert _canonical_remote(None) is None


def test_scan_detects_project(tmp_path):
    proj = tmp_path / "2026" / "20260516-OMNIPULSE"
    proj.mkdir(parents=True)
    (proj / "package.json").write_text('{"name":"omnipulse","dependencies":{"next":"15"}}')
    (proj / "README.md").write_text("# OMNIPULSE\nCentral OM platform.\n")
    rows = scan_projects(str(tmp_path))
    row = next(r for r in rows if r["name"] == "20260516-OMNIPULSE")
    assert row["stack"] == "Next.js"
    assert row["purpose_oneliner"].startswith("OMNIPULSE") or "OM platform" in row["purpose_oneliner"]
    assert row["path"] == str(proj)
    assert row["git_remote"] is None
    assert row["org"] == "om"


def test_identity_uses_git_remote(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "README.md").write_text("# P")
    with patch("scripts.ingest._git_remote", return_value="OuPDO/p"):
        row = next(r for r in scan_projects(str(tmp_path)) if r["name"] == "p")
    assert row["path"] == "OuPDO/p"
    assert row["git_remote"] == "OuPDO/p"
    assert row["fs_path"] == str(proj)


def test_identity_falls_back_to_path_without_remote(tmp_path):
    proj = tmp_path / "q"
    proj.mkdir()
    (proj / "README.md").write_text("# Q")
    with patch("scripts.ingest._git_remote", return_value=None):
        row = next(r for r in scan_projects(str(tmp_path)) if r["name"] == "q")
    assert row["path"] == str(proj)
    assert row["fs_path"] == str(proj)
