from unittest.mock import MagicMock, patch
import memory.registry as registry

def test_query_projects_filters_by_org():
    fake = MagicMock()
    chain = fake.table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value.execute.return_value.data = [{"name": "OMNIPULSE", "org": "om"}]
    with patch.object(registry, "get_supabase", return_value=fake):
        rows = registry.query_projects(org="om")
    fake.table.assert_called_with("odin_project_registry")
    chain.eq.assert_any_call("org", "om")
    assert rows == [{"name": "OMNIPULSE", "org": "om"}]
