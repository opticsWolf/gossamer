import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "rust" / "app" / "target" / "debug" / (
    "gossamer-mcp.exe" if sys.platform == "win32" else "gossamer-mcp"
)

pytestmark = pytest.mark.skipif(not EXE.exists(), reason="gossamer-mcp binary is not built")


PROTOCOL_REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "resources/read",
        "params": {"uri": "skills://gossamer"},
    },
]


def run_rpc(requests, expected_ids, cache_dir=None):
    env = dict(os.environ)
    if cache_dir is not None:
        env["GOSSAMER_CACHE_DIR"] = str(cache_dir)
    process = subprocess.Popen(
        [str(EXE)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    for request in requests:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
    responses = {}
    for _ in expected_ids:
        line = process.stdout.readline()
        if not line:
            break
        response = json.loads(line)
        responses[response.get("id")] = response
    process.stdin.close()
    process.terminate()
    return responses


def test_protocol_fixture_matches_rust_server():
    loaded = json.loads((ROOT / "rust/fixtures/mcp_protocol.json").read_text(encoding="utf-8"))
    expected = {int(key): value for key, value in loaded.items()}
    with tempfile.TemporaryDirectory() as cache_dir:
        actual = run_rpc(PROTOCOL_REQUESTS, [1, 2, 3, 4, 5], cache_dir)
    assert actual == expected


def _python_tools(cache_dir):
    pytest.importorskip("mcp")
    process = subprocess.Popen(
        [sys.executable, "-m", "gossamer.mcp_server"],
        env={**os.environ, "GOSSAMER_CACHE_DIR": str(cache_dir)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        process.stdout.readline()
        return json.loads(process.stdout.readline())["result"]["tools"]
    finally:
        process.stdin.close()
        process.terminate()
        process.wait(timeout=10)


def test_tools_list_schema_matches_python_server():
    loaded = json.loads((ROOT / "rust/fixtures/mcp_protocol.json").read_text(encoding="utf-8"))
    expected = loaded["3"]["result"]["tools"]
    with tempfile.TemporaryDirectory() as cache_dir:
        actual = _python_tools(cache_dir)
    assert [tool["name"] for tool in actual] == [tool["name"] for tool in expected]
    assert actual == expected


def test_tool_call_fixture_matches_rust_server():
    cases = json.loads((ROOT / "rust/fixtures/mcp_tool_calls.json").read_text(encoding="utf-8"))
    requests = [
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {"name": case["name"], "arguments": case["arguments"]},
        }
        for index, case in enumerate(cases, 1)
    ]
    with tempfile.TemporaryDirectory() as cache_dir:
        responses = run_rpc(requests, range(1, len(cases) + 1), cache_dir)
    for index, case in enumerate(cases, 1):
        result = responses[index]["result"]
        assert result["content"][0]["text"] == case["text"]
        assert result["isError"] == case["isError"]
