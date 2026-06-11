"""call_candidate 后端调度 + call_openai_compat 协议层：mock chat/completions 端到端。"""
import json
import os
import subprocess
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CALL_CANDIDATE = os.path.join(ROOT, "bin", "call_candidate.sh")
CALL_OPENAI = os.path.join(ROOT, "bin", "call_openai_compat.sh")


class _EchoHandler(BaseHTTPRequestHandler):
    """把请求 payload 回显进 content，便于断言协议字段（model/temperature/enable_thinking）。"""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = json.dumps(
            {
                "model": body.get("model"),
                "temperature": body.get("temperature"),
                "enable_thinking": body.get("enable_thinking", "ABSENT"),
                "user_content": body["messages"][0]["content"],
                "auth": self.headers.get("Authorization", "NONE"),
            },
            ensure_ascii=False,
        )
        resp = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp.encode())

    def log_message(self, *args):  # 静音
        pass


def _run(script, args, prompt, extra_env=None):
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [script, *args], input=prompt, capture_output=True, text=True, env=env, timeout=30
    )


class TestOpenAICompatProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _echo(self, args, prompt="测试问题", extra_env=None):
        proc = _run(
            CALL_OPENAI,
            ["--base-url", self.base_url, "--no-cache", *args],
            prompt,
            extra_env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_payload_protocol(self):
        echo = self._echo(["--model", "test-m"])
        self.assertEqual(echo["model"], "test-m")
        self.assertEqual(echo["temperature"], 0)          # 确定性优先
        self.assertEqual(echo["enable_thinking"], "ABSENT")  # 未显式设置 → 省略字段
        self.assertEqual(echo["user_content"], "测试问题")

    def test_think_off_injects_enable_thinking(self):
        echo = self._echo(["--model", "test-m", "--think", "off"])
        self.assertIs(echo["enable_thinking"], False)

    def test_api_key_header_via_env_indirection(self):
        echo = self._echo(
            ["--model", "test-m", "--api-key-env", "FAKE_KEY_VAR"],
            extra_env={"FAKE_KEY_VAR": "sk-test-123"},
        )
        self.assertEqual(echo["auth"], "Bearer sk-test-123")

    def test_no_key_means_no_auth_header(self):
        echo = self._echo(["--model", "test-m", "--api-key-env", "NO_SUCH_VAR_XYZ"])
        self.assertEqual(echo["auth"], "NONE")  # litellm 本地代理可无鉴权

    def test_missing_model_fails_fast(self):
        proc = _run(CALL_OPENAI, ["--base-url", self.base_url, "--no-cache"], "hi",
                    {"OPENAI_MODEL": ""})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("模型名", proc.stderr)

    def test_empty_stdin_fails_fast(self):
        proc = _run(CALL_OPENAI, ["--base-url", self.base_url, "--model", "m", "--no-cache"], "")
        self.assertNotEqual(proc.returncode, 0)

    def test_cache_round_trip(self):
        # 唯一 prompt → 首跑写缓存；命中后零网络（用错 base_url 仍应答对）
        prompt = f"缓存测试 {uuid.uuid4()}"
        proc1 = _run(CALL_OPENAI, ["--base-url", self.base_url, "--model", "test-m"], prompt)
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        proc2 = _run(
            CALL_OPENAI,
            ["--base-url", "http://127.0.0.1:9/v1", "--model", "test-m"],  # 不可达端口
            prompt,
        )
        # 缓存键含 base_url → 换 base_url 不命中，应失败；同 base_url 才命中
        self.assertNotEqual(proc2.returncode, 0)
        proc3 = _run(CALL_OPENAI, ["--base-url", self.base_url, "--model", "test-m"], prompt)
        self.assertEqual(proc3.returncode, 0)
        self.assertEqual(proc1.stdout, proc3.stdout)


class TestCandidateDispatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_unknown_backend_fails_fast(self):
        proc = _run(CALL_CANDIDATE, ["--backend", "nosuch", "--no-cache"], "hi")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("未知候选后端", proc.stderr)

    def test_litellm_preset_routes_to_openai_compat(self):
        proc = _run(
            CALL_CANDIDATE,
            ["--backend", "litellm", "--model", "test-m", "--no-cache"],
            "hi",
            {"LITELLM_BASE_URL": self.base_url},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["model"], "test-m")

    def test_siliconflow_preset_sends_its_key(self):
        proc = _run(
            CALL_CANDIDATE,
            ["--backend", "siliconflow", "--model", "Qwen/test", "--no-cache"],
            "hi",
            {"SILICONFLOW_BASE_URL": self.base_url, "SILICONFLOW_API_KEY": "sk-sf-1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["auth"], "Bearer sk-sf-1")

    def test_passthrough_base_url_overrides_preset(self):
        # 透传的 --base-url 在预设之后解析 → 覆盖预设（扩展点：临时指向任意端点）
        proc = _run(
            CALL_CANDIDATE,
            ["--backend", "openai", "--model", "m", "--base-url", self.base_url, "--no-cache"],
            "hi",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
