"""Read-only TBS reviewer executed over SSH on the MacBook.

The caller sends a bounded diff on stdin. No checkout, GitHub token, or Studio
credential is copied to this host. This file is sent as source by the caller.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


ROOT = Path.home() / "Developer/thinking-brain-school"
PATH = str(Path.home() / ".local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def main():
    try:
        request = json.loads(sys.stdin.read(250001))
        provider = request["provider"]
        prompt = request["prompt"]
        budget = min(max(int(request["timeout_s"]), 1), 180)
        if provider not in ("codex", "claude") or len(prompt) > 220000:
            raise ValueError("invalid request")
        env = dict(os.environ, PATH=PATH)
        if provider == "codex":
            env["CODEX_HOME"] = str(ROOT / ".codex-tbs-account")
            binary = "/opt/homebrew/bin/codex"
            auth = subprocess.run([binary, "login", "status"], env=env, capture_output=True,
                                  text=True, timeout=8)
            signed_in = auth.returncode == 0 and "Logged in" in auth.stdout
        else:
            env["CLAUDE_CONFIG_DIR"] = str(ROOT / ".claude-tbs-account")
            binary = str(Path.home() / ".local/bin/claude")
            auth = subprocess.run([binary, "auth", "status"], env=env, capture_output=True,
                                  text=True, timeout=8)
            try:
                signed_in = auth.returncode == 0 and json.loads(auth.stdout).get("loggedIn") is True
            except ValueError:
                signed_in = False
        if not signed_in:
            print(json.dumps({"status": "auth_unavailable", "provider": provider}))
            return
        if request.get("probe_only") is True:
            print(json.dumps({"status": "ready", "provider": provider}))
            return
        with tempfile.TemporaryDirectory(prefix="nexus-remote-review-") as work:
            if provider == "codex":
                out = str(Path(work) / "answer.txt")
                command = [binary, "exec", "--ephemeral", "--model", "gpt-6-astra",
                           "--sandbox", "read-only", "--skip-git-repo-check", "-C", work,
                           "--output-last-message", out, prompt]
            else:
                command = [binary, "-p", prompt, "--model", "opus", "--max-turns", "1",
                           "--tools", "", "--output-format", "text"]
            process = subprocess.Popen(command, cwd=work, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, _ = process.communicate(timeout=budget)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                print(json.dumps({"status": "timeout", "provider": provider}))
                return
            answer = Path(out).read_text() if provider == "codex" and Path(out).exists() else stdout
            print(json.dumps({"status": "ok" if process.returncode == 0 else "provider_failed",
                              "provider": provider, "text": answer[-15000:]}))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print(json.dumps({"status": "unavailable"}))


if __name__ == "__main__":
    main()
