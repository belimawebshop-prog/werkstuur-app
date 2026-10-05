"""Exercise status counts against the actual session schema without credentials."""
import ast
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import re
from types import SimpleNamespace
import threading
import time
import unittest


class Query:
    def __init__(self, table, rows, columns):
        self.table, self.rows, self.columns = table, rows, columns
        self.filters = []

    def select(self, column, count=None):
        if column not in self.columns:
            raise RuntimeError(f"column {self.table}.{column} does not exist")
        return self

    def limit(self, count):
        return self

    def eq(self, column, value):
        self.filters.append(lambda row: row[column] == value)
        return self

    def gt(self, column, value):
        self.filters.append(lambda row: row[column] > value)
        return self

    def execute(self):
        rows = [row for row in self.rows if all(test(row) for test in self.filters)]
        return SimpleNamespace(data=rows[:1], count=len(rows))


class StatusTests(unittest.TestCase):
    def setUp(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        data = {
            "settings": [{"key": "fixture"}],
            "cases": [{"id": 5, "organization_id": 3}, {"id": 6, "organization_id": 3}],
            "users": [{"id": 7, "organization_id": 3, "active": True}],
            "attachments": [{"id": 1, "organization_id": 3}],
            "pilots": [],
            "organizations": [{"id": 1}, {"id": 2}, {"id": 3}],
            "sessions": [
                {"user_id": 1, "active_organization_id": 3, "expires_at": future},
                {"user_id": 2, "active_organization_id": 3, "expires_at": future},
                {"user_id": 3, "active_organization_id": 3, "expires_at": past},
                {"user_id": 4, "active_organization_id": 1, "expires_at": future},
            ],
        }
        columns = {name: {"id"} for name in data}
        columns["settings"] = {"key"}
        # sessions has a token_hash primary key and no id column.
        columns["sessions"] = {"token_hash", "user_id", "active_organization_id", "expires_at", "created_at"}
        self.storage_calls = []
        self.ns = {
            "datetime": datetime, "timezone": timezone, "time": time, "os": os, "re": re,
            "_STATUS_LOCK": threading.Lock(), "_LAST_SERVER_ERROR": None,
            "_LAST_SUCCESSFUL_STATUS_CHECK": None, "_PROCESS_STARTED_AT": time.time(),
            "_PROCESS_STARTED_ISO": datetime.now(timezone.utc).isoformat(),
            "APP_NAME": "Werkstuur", "APP_VERSION": "test", "APP_BUILD": "test",
            "BUCKET": "preflight-attachments", "current_org_id": lambda: 3,
            "current_organization": lambda: {"id": 3}, "resp_data": lambda result: result.data,
            "_reset_supabase_client": lambda: None,
            "mail_status_payload": lambda: {"status": "ready"},
            "sb": SimpleNamespace(
                table=lambda name: Query(name, data[name], columns[name]),
                storage=SimpleNamespace(from_=lambda bucket: SimpleNamespace(list=lambda prefix, options: self.storage_calls.append((bucket, prefix, options)))),
            ),
        }
        tree = ast.parse((Path(__file__).parent / "cloud_server.py").read_text())
        names = {"_count_rows", "system_status_payload", "_record_status_success", "_record_server_error", "_sanitize_error_message"}
        nodes = [node for node in tree.body if getattr(node, "name", "") in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "cloud_server.py", "exec"), self.ns)

    def test_session_count_excludes_expired_and_other_organizations(self):
        self.assertEqual(self.ns["_count_rows"]("sessions", [("eq", "active_organization_id", 3), ("gt", "expires_at", datetime.now(timezone.utc).isoformat())]), 2)

    def test_schema_compatible_counts_leave_status_healthy(self):
        result = self.ns["system_status_payload"]({"is_platform_owner": True})
        self.assertEqual(result["overall"], "healthy")
        self.assertEqual(result["database"]["status"], "online")
        self.assertEqual(result["storage"]["status"], "online")
        self.assertEqual(result["counts"], {"cases": 2, "active_users": 1, "active_sessions": 2, "attachments": 1, "pilots": 0, "organizations": 3})
        self.assertIsNone(result["monitoring"]["last_error"])
        self.assertIsNotNone(result["monitoring"]["last_successful_check"])
        self.assertEqual(self.storage_calls, [("preflight-attachments", "org/3", {"limit": 1, "offset": 0})])


if __name__ == "__main__":
    unittest.main(verbosity=2)
