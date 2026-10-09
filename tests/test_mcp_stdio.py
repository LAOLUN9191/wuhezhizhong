"""Real stdio transport and worker threads, with synthetic credentials/provider only."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
import json,sys,time
sys.path.insert(0, sys.argv[1])
import server
server._load_api_keys=lambda:{server.KEY_NAMES["route_a_group_1"]:"synthetic-smoke-key"}
def provider(provider,role,task,cwd,model,**kwargs):
    time.sleep(0.05)
    aid=kwargs["activity_id"]
    server._set_activity_state(aid,sessionId="smoke-session",currentProviderId=provider)
    text="continued" if kwargs.get("session_id") else "X"*12000+"TAIL"
    return 0,json.dumps({"item":{"type":"agent_message","text":text}}),""
server._run_provider=provider
server.serve()
'''


class MCPStdioTests(unittest.TestCase):
    def test_handshake_async_launch_paging_ack_and_continuation(self):
        with TemporaryDirectory(prefix=".test-stdio-", dir=ROOT) as folder:
            fixture = Path(folder)
            config = fixture / "user_config.json"
            config.write_text(json.dumps({
                "paths": {"workspace": str(ROOT), "state": str(fixture / "state.json"),
                          "diagnostic_log": str(fixture / "log.jsonl"), "legacy_state": str(fixture / "legacy.json")},
                "providers": {"route_a_group_1": {"model": "smoke-model"}},
                "routes": [{"id": "route_a", "name": "A", "enabled": True, "native_codex_fallback": False,
                            "groups": [{"provider_id": "route_a_group_1", "enabled": True, "auto": True}]}],
            }), encoding="utf-8")
            env = {**os.environ, "LAOWU_USER_CONFIG_PATH": str(config), "CODEX_HOME": str(fixture / "codex")}
            env.pop("LAOWU_DISPATCH_CHILD", None)
            process = subprocess.Popen([sys.executable, "-B", "-u", "-c", HARNESS, str(ROOT / "tools/laowu_mcp")],
                                       cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8")
            messages = queue.Queue()
            errors = []
            def read():
                for line in process.stdout:
                    messages.put(json.loads(line))
            reader = threading.Thread(target=read)
            error_reader = threading.Thread(target=lambda: errors.append(process.stderr.read()))
            reader.start()
            error_reader.start()
            counter = 0
            def request(method, params):
                nonlocal counter
                counter += 1
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = messages.get(timeout=max(0.01, deadline-time.monotonic()))
                    if message.get("id") == counter:
                        self.assertNotIn("error", message)
                        return message["result"]
                self.fail("MCP response timed out")
            def call(name, **arguments):
                return request("tools/call", {"name": name, "arguments": arguments})
            def terminal(aid):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = call("laowu_task_result", action="get", activity_id=aid)
                    self.assertFalse(result.get("isError", False), result)
                    data = result["structuredContent"]
                    if data["ready"]:
                        return data
                    time.sleep(0.01)
                self.fail("Worker did not finish")
            try:
                init = request("initialize", {"protocolVersion": "2025-06-18"})
                self.assertTrue(init["capabilities"]["tools"]["listChanged"])
                self.assertIn("compare it with the original request", init["instructions"])
                self.assertIn("Do not create a duplicate task", init["instructions"])
                schemas = {tool["name"]: tool for tool in request("tools/list", {})["tools"]}
                self.assertIn("offset", schemas["laowu_task_result"]["inputSchema"]["properties"])
                self.assertNotIn("concurrency", schemas["laowu_profiles"]["inputSchema"]["properties"]["action"]["enum"])
                launch = call("run_subagent_reviewer", cwd=str(ROOT), task="smoke", group="auto")
                aid = launch["structuredContent"]["activity_id"]
                first = terminal(aid)
                self.assertFalse(first["complete"])
                self.assertTrue(call("laowu_task_result", action="acknowledge", activity_id=aid)["isError"])
                second = call("laowu_task_result", action="get", activity_id=aid, offset=first["next_offset"])["structuredContent"]
                self.assertTrue((first["result"] + second["result"]).endswith("X" * 12000 + "TAIL"))
                self.assertTrue(call("laowu_task_result", action="acknowledge", activity_id=aid)["structuredContent"]["acknowledged"])
                continued = call("laowu_continue_task", activity_id=aid, message="follow-up")
                self.assertEqual(continued["structuredContent"]["activity_id"], aid)
                self.assertEqual(terminal(aid)["result"], "continued")
                call("laowu_task_result", action="acknowledge", activity_id=aid)
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(5)
                error_reader.join(5)
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0, "".join(errors))
