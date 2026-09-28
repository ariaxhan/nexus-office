"""Which tier did the work (#235 D/5): nothing -> deterministic -> local -> cheap-hosted -> frontier -> human.

`local_task` is the one seam for a bounded local-model transform: an explicit schema, a validator,
and a fallback that names the tier it escalates to. Every delegated operation appends one record
(operation, tier, model, latency, validator verdict, escalation reason) so frontier dependence is a
gradient you can count, not a feeling. A token count or cost is recorded only when measured.

  python3 -m nexus.delegation [--since DAYS]    calls / tokens / cost reaching each tier
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

TIERS = ("deterministic", "local", "cheap-hosted", "frontier", "human")
LOG = os.environ.get("NEXUS_DELEGATION_LOG",
                     os.path.expanduser("~/Library/Application Support/nexus/delegation.jsonl"))
OLLAMA = os.environ.get("NEXUS_OLLAMA", "http://127.0.0.1:11434")


def record(operation, tier, *, model=None, latency_ms=None, ok=None, validator=None, escalation=None,
           tokens=None, cost_usd=None, provenance=None, outcome=None, log=None):
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    row = {"ts": round(time.time(), 3), "operation": operation, "tier": tier, "model": model,
           "latency_ms": latency_ms, "ok": ok, "validator": validator, "escalation": escalation,
           "tokens": tokens, "cost_usd": cost_usd, "provenance": provenance, "outcome": outcome}
    path = log or LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _ollama(model, prompt, schema, timeout):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": schema,
                       "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        reply = json.loads(resp.read())
    return json.loads(reply["response"]), reply.get("prompt_eval_count", 0) + reply.get("eval_count", 0)


def _conforms(value, schema):
    if not isinstance(value, dict):
        return "not an object"
    for key in schema.get("required", []):
        if key not in value:
            return f"missing {key}"
    kinds = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}
    for key, spec in schema.get("properties", {}).items():
        if key in value and "type" in spec and not isinstance(value[key], kinds.get(spec["type"], object)):
            return f"{key} is not {spec['type']}"
        if key in value and "enum" in spec and value[key] not in spec["enum"]:
            return f"{key} not in enum"
    return None


def local_task(operation, schema, data, *, prompt, model, validator=None, deterministic=None, fallback,
               timeout=60, log=None):
    """(result, record). Deterministic first; the local model only when that abstains (returns None);
    a local answer counts only if it conforms to `schema` and passes `validator(data, result)`
    (None or a reason string). Otherwise `fallback(data, reason)` returns (result, tier)."""
    t = time.time()
    if deterministic is not None:
        result = deterministic(data)
        if result is not None:
            return result, record(operation, "deterministic", latency_ms=int((time.time() - t) * 1000), ok=True, log=log)
    reason, tokens = None, None
    try:
        result, tokens = _ollama(model, prompt(data), schema, timeout)
        reason = _conforms(result, schema) or (validator(data, result) if validator else None)
    except (OSError, ValueError, KeyError) as exc:
        reason = f"local unavailable: {type(exc).__name__}"
    ms = int((time.time() - t) * 1000)
    if reason is None:
        return result, record(operation, "local", model=model, latency_ms=ms, ok=True, validator="pass",
                              tokens=tokens, log=log)
    result, tier = fallback(data, reason)
    return result, record(operation, tier, model=model, latency_ms=ms, ok=result is not None,
                          validator="fail", escalation=reason, tokens=tokens, log=log)


def gradient(since_days=None, log=None):
    """Per tier: calls, measured tokens, measured cost, and how many calls had neither measured."""
    cutoff = time.time() - since_days * 86400 if since_days else 0
    out = {tier: {"calls": 0, "tokens": 0, "cost_usd": 0.0, "unmeasured": 0} for tier in TIERS}
    path = log or LOG
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ts", 0) < cutoff or row.get("tier") not in out:
                    continue
                t = out[row["tier"]]
                t["calls"] += 1
                t["tokens"] += row.get("tokens") or 0
                t["cost_usd"] = round(t["cost_usd"] + (row.get("cost_usd") or 0), 4)
                t["unmeasured"] += row.get("tokens") is None and row.get("cost_usd") is None
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float)
    print(json.dumps(gradient(ap.parse_args().since), indent=1))
