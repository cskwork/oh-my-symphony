"""Fake ACP agent for `tests/test_backend_acp.py`.

Speaks newline-delimited JSON-RPC 2.0 on stdio like a real Agent Client
Protocol agent. `argv[1]` selects the scenario; every message received is
appended as one JSON line to `$FAKE_ACP_LOG` so tests can assert what the
client sent. `$FAKE_ACP_CHILD_PID` (hang mode) receives the pid of a
grandchild sleeper so tests can prove the whole process group was killed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
RESUME = "--resume" in sys.argv
LOG = os.environ.get("FAKE_ACP_LOG")
_next_id = 1000
_turns = 0


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def log(msg: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg) + "\n")


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    log(msg)
    return msg


def update(session_id: str, body: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session_id, "update": body}})


def chunk(session_id: str, text: str) -> None:
    update(session_id, {"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text}})


def call_client(method: str, params: dict) -> dict:
    """Send a request to the client and block until its response arrives."""
    global _next_id
    _next_id += 1
    req_id = _next_id
    send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    while True:
        msg = read()
        if msg is None:
            sys.exit(0)
        if msg.get("id") == req_id and "method" not in msg:
            return msg


def handle_prompt(msg: dict) -> None:
    global _turns
    _turns += 1
    params = msg["params"]
    sid = params["sessionId"]
    prompt = "".join(block.get("text", "") for block in params["prompt"])
    reply = lambda reason: send({"jsonrpc": "2.0", "id": msg["id"],  # noqa: E731
                                 "result": {"stopReason": reason}})
    if MODE == "error":
        send({"jsonrpc": "2.0", "id": msg["id"],
              "error": {"code": -32603, "message": "model exploded"}})
        return
    if MODE == "refusal":
        chunk(sid, "no")
        reply("refusal")
        return
    if MODE in ("hang", "hang-cancel"):
        child = subprocess.Popen(["sleep", "300"])
        if os.environ.get("FAKE_ACP_CHILD_PID"):
            with open(os.environ["FAKE_ACP_CHILD_PID"], "w", encoding="utf-8") as fh:
                fh.write(str(child.pid))
        chunk(sid, "working")
        while True:
            nxt = read()
            if nxt is None:
                sys.exit(0)
            if nxt.get("method") == "session/cancel" and MODE == "hang-cancel":
                reply("cancelled")
                return
    if MODE == "permission":
        perm = call_client("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": "call_1", "title": "Edit file", "kind": "edit"},
            "options": [
                {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                {"optionId": "yes-always", "name": "Always", "kind": "allow_always"},
                {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
            ],
        })
        danger = call_client("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": "call_2", "title": "rm", "kind": "execute",
                         "rawInput": {"command": "rm -rf /"}},
            "options": [
                {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                {"optionId": "no", "name": "Reject", "kind": "reject_once"},
            ],
        })
        fs = call_client("fs/read_text_file", {"sessionId": sid, "path": "/etc/passwd"})
        chunk(sid, "perm=" + perm["result"]["outcome"]["optionId"])
        chunk(sid, ";danger=" + danger["result"]["outcome"]["optionId"])
        chunk(sid, ";fs=" + str(fs["error"]["code"]))
        reply("end_turn")
        return
    if MODE == "malformed":
        sys.stdout.write("this is not json\n{broken\n")
        sys.stdout.flush()
    if MODE == "garbage":
        for i in range(12):
            sys.stdout.write(f"garbage {i}\n")
        sys.stdout.flush()
        while read() is not None:
            pass
        sys.exit(0)
    update(sid, {"sessionUpdate": "agent_thought_chunk",
                 "content": {"type": "text", "text": "thinking"}})
    update(sid, {"sessionUpdate": "tool_call", "toolCallId": "c1",
                 "title": "Read file", "kind": "read", "status": "pending"})
    update(sid, {"sessionUpdate": "brand_new_kind_from_the_future", "x": 1})
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid}})
    update(sid, {"sessionUpdate": "usage_update", "used": 1200, "size": 200000,
                 "cost": {"amount": 0.01, "currency": "USD"}})
    chunk(sid, f"turn {_turns} ")
    chunk(sid, f"pid {os.getpid()} ")
    chunk(sid, f"echo {prompt}")
    reply("end_turn")
    if MODE == "die-after-turn":
        sys.exit(0)


def main() -> None:
    sessions = 0
    while True:
        msg = read()
        if msg is None:
            return
        method = msg.get("method")
        if method == "initialize":
            if MODE == "silent-init":
                continue
            caps: dict = {"loadSession": False}
            if RESUME:
                caps["sessionCapabilities"] = {"resume": {}}
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": 1, "agentCapabilities": caps, "authMethods": []}})
        elif method == "session/new":
            sessions += 1
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "result": {"sessionId": f"sess-{os.getpid()}-{sessions}"}})
        elif method == "session/resume":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif method == "session/prompt":
            handle_prompt(msg)
        elif "id" in msg:
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "error": {"code": -32601, "message": "unknown"}})


if __name__ == "__main__":
    main()
