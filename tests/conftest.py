"""Pytest-Konfiguration fuer odin-core Tests.

Import-Pfad wird ueber `pythonpath = .` in pytest.ini gesetzt.
`fake_supabase` liefert einen fluent-chain-kompatiblen Supabase-Doppel, der
Inserts/Updates aufzeichnet und vordefinierte Select-Zeilen zurueckgibt.
"""
from unittest.mock import MagicMock

import pytest


class _FakeChain:
    """Fluent select/filter-Kette; ignoriert Filter, gibt preset-Zeilen zurueck."""
    # Einschraenkung: eq/is_/limit-Argumente werden ignoriert -- Spaltenname-Korrektheit (z.B. is_("valid_to","null")) wird von diesen Tests NICHT verifiziert.

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def is_(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        m = MagicMock()
        m.data = self._rows
        return m


class _FakeUpdate:
    def __init__(self, parent, patch):
        self.parent = parent
        self.patch = patch

    def eq(self, col, val):
        self.parent.updates.append((col, val, self.patch))
        return self

    def execute(self):
        return MagicMock()


class _FakeTable:
    def __init__(self, parent):
        self.parent = parent

    def select(self, *a):
        return _FakeChain(self.parent.select_rows)

    def insert(self, row):
        self.parent.inserts.append(row)
        new = dict(row)
        new.setdefault("id", self.parent.next_id())
        new.setdefault("valid_from", "2026-07-02T00:00:00+00:00")
        exec_mock = MagicMock()
        exec_mock.execute.return_value.data = [new]
        return exec_mock

    def update(self, patch):
        return _FakeUpdate(self.parent, patch)


class FakeSupabase:
    """Zeichnet inserts/updates/rpc-Calls auf; select gibt `select_rows` zurueck."""

    def __init__(self, select_rows=None):
        self.select_rows = select_rows or []
        self.inserts: list[dict] = []
        self.updates: list[tuple] = []
        self.rpc_calls: list[tuple] = []
        self._n = 0

    def next_id(self) -> str:
        self._n += 1
        return f"mem-{self._n}"

    def table(self, name):
        return _FakeTable(self)

    def rpc(self, name, params):
        """Simuliert odin_memory_supersede: gibt eine neue Zeile mit generierter id zurueck.
        Hinweis: Dieser Fake prueft nur DELEGATION; echte Atomaritaet, UNIQUE-Index-Verletzung und PostgREST-Shape werden live in Task 7 verifiziert."""
        self.rpc_calls.append((name, params))
        new = {
            "id": self.next_id(),
            "kind": params.get("p_kind"),
            "content": params.get("p_content"),
            "subject": params.get("p_subject"),
            "key": params.get("p_key"),
            "org": params.get("p_org"),
            "provenance": params.get("p_provenance"),
            "content_hash": params.get("p_content_hash"),
            "valid_from": params.get("p_valid_from") or "2026-07-02T00:00:00+00:00",
        }
        exec_mock = MagicMock()
        exec_mock.execute.return_value.data = new
        return exec_mock


@pytest.fixture
def fake_supabase():
    """Factory: fake_supabase(select_rows=[...]) -> FakeSupabase."""
    def _make(select_rows=None):
        return FakeSupabase(select_rows=select_rows)
    return _make
