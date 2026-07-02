from scripts.ingest import chunk_text, collect_sources, _source_type


def test_chunk_overlap():
    text = "wort " * 500
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_source_type_mapping():
    assert _source_type("README.md") == "readme"
    assert _source_type("CLAUDE.md") == "claude_md"
    assert _source_type("docs/a.md") == "doc"
    assert _source_type("wiki/b.md") == "wiki"
    assert _source_type("outputs/c.md") == "output"
    assert _source_type("10_ADO/note.md") == "note"


def test_collect_sources_all_md(tmp_path):
    (tmp_path / "README.md").write_text("r")
    (tmp_path / "wiki").mkdir(); (tmp_path / "wiki" / "w.md").write_text("w")
    (tmp_path / "10_ADO").mkdir(); (tmp_path / "10_ADO" / "n.md").write_text("n")
    (tmp_path / ".git").mkdir(); (tmp_path / ".git" / "x.md").write_text("skip")
    (tmp_path / "node_modules").mkdir(); (tmp_path / "node_modules" / "y.md").write_text("skip")
    got = {s[0] for s in collect_sources(str(tmp_path))}
    paths = {s[1] for s in collect_sources(str(tmp_path))}
    assert {"readme", "wiki", "note"} <= got
    assert not any(".git" in p or "node_modules" in p for p in paths)


def test_collect_sources_picks_readme(tmp_path):
    (tmp_path / "README.md").write_text("hallo")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.md").write_text("nope")
    (tmp_path / "docs" / "node_modules").mkdir(parents=True)
    (tmp_path / "docs" / "node_modules" / "skip.md").write_text("nope")
    srcs = collect_sources(str(tmp_path))
    assert any(s[0] == "readme" for s in srcs)
    assert not any("node_modules" in s[1] for s in srcs)
