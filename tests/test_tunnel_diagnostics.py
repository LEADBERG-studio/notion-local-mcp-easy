"""Tests for the tunnel process doctor added in 2.4.0.

The rule under test: the tunnel the launcher currently owns is never called an
orphan and is never offered for termination.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from connections import diagnostics  # noqa: E402

SSH_COMMAND = (
    "ssh -N -T -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes "
    "-i C:\\Users\\me\\.ssh\\mcp -p 2222 -R mcp:80:127.0.0.1:8765 tunnel@tunnellio.site"
)
STALE_COMMAND = (
    "ssh -N -T -o ExitOnForwardFailure=yes -i C:\\Users\\me\\.ssh\\old "
    "-p 2222 -R stale:80:127.0.0.1:9999 tunnel@tunnellio.site"
)


def fake_processes(*entries):
    return mock.patch.object(diagnostics, "_powershell_process_list", return_value=list(entries))


class DiagnosticsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime_file = Path(self._tmp.name) / "runtime.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write_runtime(self, **extra):
        payload = {
            "tunnel_pid": 111,
            "server_pid": 222,
            "url": "https://mcp.tunnellio.site/mcp",
        }
        payload.update(extra)
        self.runtime_file.write_text(json.dumps(payload), encoding="utf-8")


class ScanTests(DiagnosticsTestCase):
    def test_the_running_tunnel_is_marked_live(self):
        self.write_runtime()
        with fake_processes({"ProcessId": 111, "Name": "ssh.exe", "CommandLine": SSH_COMMAND}):
            processes = diagnostics.scan(self.runtime_file)
        self.assertEqual(len(processes), 1)
        self.assertTrue(processes[0].is_live)
        self.assertFalse(processes[0].is_orphan)
        self.assertEqual(processes[0].claimed_by, "https://mcp.tunnellio.site/mcp")

    def test_a_leftover_process_is_an_orphan(self):
        self.write_runtime()
        with fake_processes(
            {"ProcessId": 111, "Name": "ssh.exe", "CommandLine": SSH_COMMAND},
            {"ProcessId": 999, "Name": "ssh.exe", "CommandLine": STALE_COMMAND},
        ):
            processes = diagnostics.scan(self.runtime_file)
        stray = diagnostics.orphans(processes)
        self.assertEqual([item.pid for item in stray], [999])

    def test_everything_is_an_orphan_when_no_launcher_runs(self):
        with fake_processes({"ProcessId": 111, "Name": "ssh.exe", "CommandLine": SSH_COMMAND}):
            processes = diagnostics.scan(self.runtime_file)
        self.assertTrue(processes[0].is_orphan)

    def test_live_process_is_listed_first(self):
        self.write_runtime(tunnel_pid=999)
        with fake_processes(
            {"ProcessId": 111, "Name": "ssh.exe", "CommandLine": SSH_COMMAND},
            {"ProcessId": 999, "Name": "ssh.exe", "CommandLine": STALE_COMMAND},
        ):
            processes = diagnostics.scan(self.runtime_file)
        self.assertEqual(processes[0].pid, 999)
        self.assertTrue(processes[0].is_live)

    def test_managed_client_processes_are_included(self):
        self.write_runtime(tunnel_pid=0)
        with fake_processes(
            {"ProcessId": 777, "Name": "tunnellio.exe", "CommandLine": "tunnellio.exe bridge --run"}
        ):
            processes = diagnostics.scan(self.runtime_file)
        self.assertEqual(processes[0].image, "tunnellio.exe")
        self.assertTrue(processes[0].is_orphan)

    def test_workspace_hint_is_recorded(self):
        self.write_runtime(tunnel_pid=0)
        with fake_processes(
            {"ProcessId": 5, "Name": "ssh.exe", "CommandLine": "ssh -R x:80:127.0.0.1:1 E:\\project"}
        ):
            processes = diagnostics.scan(self.runtime_file, workspace="E:\\project")
        self.assertTrue(processes[0].notes)

    def test_a_broken_process_list_is_not_fatal(self):
        self.write_runtime()
        with fake_processes({"Name": "ssh.exe"}, {"ProcessId": "nope"}):
            processes = diagnostics.scan(self.runtime_file)
        self.assertEqual(processes, [])


class ParsingTests(unittest.TestCase):
    def test_remote_forward_and_port_are_extracted(self):
        process = diagnostics.TunnelProcess(pid=1, image="ssh.exe", command_line=SSH_COMMAND)
        self.assertEqual(process.remote_forward, "mcp:80:127.0.0.1:8765")
        self.assertEqual(process.local_port, "8765")

    def test_joined_remote_forward_is_handled(self):
        process = diagnostics.TunnelProcess(
            pid=1, image="ssh.exe", command_line="ssh -Rmcp:80:127.0.0.1:8765 host"
        )
        self.assertEqual(process.remote_forward, "mcp:80:127.0.0.1:8765")

    def test_missing_forward_is_empty_not_an_error(self):
        process = diagnostics.TunnelProcess(pid=1, image="tunnellio.exe", command_line="tunnellio.exe bridge")
        self.assertEqual(process.remote_forward, "")
        self.assertEqual(process.local_port, "")

    def test_powershell_date_is_made_readable(self):
        self.assertTrue(diagnostics._format_started("/Date(1754250667000)/"))
        self.assertEqual(diagnostics._format_started(None), "")
        self.assertEqual(diagnostics._format_started("None"), "")


class ReportTests(DiagnosticsTestCase):
    def test_empty_report_is_reassuring(self):
        with fake_processes():
            processes = diagnostics.scan(self.runtime_file)
        self.assertIn("No tunnel processes", diagnostics.report(processes))

    def test_report_calls_out_orphans(self):
        self.write_runtime()
        with fake_processes(
            {"ProcessId": 111, "Name": "ssh.exe", "CommandLine": SSH_COMMAND},
            {"ProcessId": 999, "Name": "ssh.exe", "CommandLine": STALE_COMMAND},
        ):
            processes = diagnostics.scan(self.runtime_file)
        text = diagnostics.report(processes)
        self.assertIn("LIVE", text)
        self.assertIn("orphan", text)
        self.assertIn("1 orphaned process", text)


class TerminateTests(unittest.TestCase):
    def test_terminate_reports_success(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(diagnostics.subprocess, "run", return_value=completed):
            ok, message = diagnostics.terminate(4242)
        self.assertTrue(ok)
        self.assertIn("4242", message)

    def test_terminate_reports_failure_without_raising(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="access denied")
        with mock.patch.object(diagnostics.subprocess, "run", return_value=completed):
            ok, message = diagnostics.terminate(4242)
        self.assertFalse(ok)
        self.assertIn("access denied", message)


if __name__ == "__main__":
    unittest.main()
