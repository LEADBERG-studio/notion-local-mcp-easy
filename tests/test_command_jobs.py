import asyncio
import contextlib
import importlib
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
os.environ["MCP_TOKEN"] = "unit-test-token"
os.environ["MCP_BASE_DIR"] = str(PROJECT)
os.environ["MCP_ALLOW_COMMANDS"] = "1"
os.environ["MCP_SERVEO_HOSTNAME"] = ""

import server as _server

server = importlib.reload(_server)


class CommandJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_allow_commands = server.ALLOW_COMMANDS
        server.ALLOW_COMMANDS = True
        server.COMMAND_JOBS.clear()
        self._clear_temp_files()

    async def asyncTearDown(self):
        for job in list(server.COMMAND_JOBS.values()):
            if job.process is not None and job.status == "running":
                await server._kill_tree(job.process)
            if job.task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(job.task, timeout=5)
        for job in list(server.COMMAND_JOBS.values()):
            server._delete_job_artifacts(job)
        server.COMMAND_JOBS.clear()
        server.ALLOW_COMMANDS = self._original_allow_commands
        self._clear_temp_files()

    def _clear_temp_files(self):
        temp_dir = server._temp_dir()
        prefixes = ("command-job-", "run-command-", "command-job-result")
        for item in temp_dir.glob("*.txt"):
            if item.name.startswith(prefixes):
                item.unlink(missing_ok=True)

    def _extract_job_id(self, text: str) -> str:
        match = re.search(r"job ([0-9a-f]+)", text)
        self.assertIsNotNone(match, text)
        return match.group(1)

    async def _wait_for_job(self, job_id: str, timeout: float = 10.0) -> str:
        deadline = asyncio.get_running_loop().time() + timeout
        latest = ""
        while asyncio.get_running_loop().time() < deadline:
            latest = await server.get_command_status(job_id=job_id)
            if "status: running" not in latest:
                return latest
            await asyncio.sleep(0.05)
        self.fail(f"job {job_id} did not finish in time; latest=\n{latest}")

    async def test_start_and_complete_background_job(self):
        started = await server.start_command(
            program="python",
            args=["-c", "print('hello from background job')"],
        )
        job_id = self._extract_job_id(started)
        finished = await self._wait_for_job(job_id)
        self.assertIn("exit code: 0", finished)
        self.assertIn("hello from background job", finished)

    async def test_cancel_running_job(self):
        started = await server.start_command(
            program="python",
            args=["-c", "import time; time.sleep(5)"],
        )
        job_id = self._extract_job_id(started)
        cancelled = await server.cancel_command(job_id=job_id)
        self.assertIn("status: cancelled", cancelled)
        status = await server.get_command_status(job_id=job_id)
        self.assertIn("status: cancelled", status)

    async def test_background_jobs_use_blocking_subprocesses(self):
        started = await server.start_command(
            program="python",
            args=["-c", "import time; time.sleep(5)"],
        )
        job_id = self._extract_job_id(started)
        try:
            job = server.COMMAND_JOBS[job_id]
            self.assertIsInstance(job.process, subprocess.Popen)
            listing = await asyncio.wait_for(server.list_commands(), timeout=1)
            self.assertIn(job_id, listing)
        finally:
            await server.cancel_command(job_id=job_id)

    async def test_status_calls_remain_responsive_while_noisy_job_runs(self):
        code = (
            "import sys, time; "
            "[sys.stdout.write('x' * 65536) or sys.stdout.flush() or time.sleep(0.01) for _ in range(40)]"
        )
        started = await server.start_command(
            program="python",
            args=["-c", code],
            timeout=20,
        )
        job_id = self._extract_job_id(started)
        try:
            for _ in range(5):
                listing = await asyncio.wait_for(server.list_commands(), timeout=1)
                self.assertIn(job_id, listing)
                status = await asyncio.wait_for(server.get_command_status(job_id=job_id), timeout=1)
                self.assertIn(job_id, status)
                await asyncio.sleep(0.05)
        finally:
            await server.cancel_command(job_id=job_id)

    async def test_list_commands_reports_jobs(self):
        started = await server.start_command(
            program="python",
            args=["-c", "print('listed job')"],
        )
        job_id = self._extract_job_id(started)
        listing = await server.list_commands()
        self.assertIn(job_id, listing)
        self.assertIn("python", listing)
        await self._wait_for_job(job_id)

    async def test_security_parity_with_run_command(self):
        original = server.ALLOW_COMMANDS
        server.ALLOW_COMMANDS = False
        try:
            with self.assertRaises(ValueError):
                await server.run_command(program="python", args=["-c", "pass"])
            with self.assertRaises(ValueError):
                await server.start_command(program="python", args=["-c", "pass"])
        finally:
            server.ALLOW_COMMANDS = original

    async def test_concurrency_cap_blocks_extra_jobs(self):
        original = server.MAX_COMMAND_JOBS
        server.MAX_COMMAND_JOBS = 1
        try:
            started = await server.start_command(
                program="python",
                args=["-c", "import time; time.sleep(5)"],
            )
            job_id = self._extract_job_id(started)
            with self.assertRaises(ValueError):
                await server.start_command(
                    program="python",
                    args=["-c", "print('second job')"],
                )
            await server.cancel_command(job_id=job_id)
        finally:
            server.MAX_COMMAND_JOBS = original

    async def test_completed_job_can_be_pruned(self):
        started = await server.start_command(
            program="python",
            args=["-c", "print('prune me')"],
        )
        job_id = self._extract_job_id(started)
        await self._wait_for_job(job_id)
        job = server.COMMAND_JOBS[job_id]
        job.finished_at = time.time() - server.JOB_RETENTION_SECONDS - 1
        server._prune_command_jobs()
        self.assertNotIn(job_id, server.COMMAND_JOBS)

    async def test_verbose_job_finishes_even_when_captured_output_is_truncated(self):
        original_limit = server.MAX_BACKGROUND_COMMAND_OUTPUT
        server.MAX_BACKGROUND_COMMAND_OUTPUT = 4096
        try:
            payload = server.MAX_BACKGROUND_COMMAND_OUTPUT + 2048
            code = (
                "import sys; "
                f"sys.stdout.write('x' * {payload}); "
                "sys.stdout.flush(); "
                "print('done')"
            )
            started = await server.start_command(
                program="python",
                args=["-c", code],
                timeout=20,
            )
            job_id = self._extract_job_id(started)
            finished = await self._wait_for_job(job_id, timeout=20)
            self.assertIn("exit code: 0", finished)
            self.assertIn("Output truncated after reaching the safe combined limit", finished)
        finally:
            server.MAX_BACKGROUND_COMMAND_OUTPUT = original_limit


if __name__ == "__main__":
    unittest.main()
