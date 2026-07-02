from config.indexed_repos import get_indexed_repos


def test_indexed_repos_shape():
    repos = get_indexed_repos()
    assert len(repos) >= 1
    for r in repos:
        assert set(r) >= {"repo", "org", "branch"}
        assert r["org"] in {"om", "ado", "do"}
        assert "/" in r["repo"]   # owner/name
