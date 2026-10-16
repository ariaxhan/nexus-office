"""Read back the Studio door pilot receipt into Tower fault state (#304).

This is deliberately a shadow bridge. It neither probes the network nor repairs
the door: Muse remains the outside observer and incumbent repair owner until
two comparable cycles and the independent absence detector are proven.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from . import faults

SOURCE = "watchdog:office-door-studio-shadow"
CLASSES = {"healthy", "door_loopback_failed", "phone_path_failed", "revision_mismatch"}
OCCURRENCE = re.compile(r"^office-door/(\d{4}-\d\d-\d\dT\d\d:(?:00|30)Z)$")


def read_door_receipt(path, now=None):
    """Validate the published effect, identity, and bounded observation age."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("door receipt must be a regular file")
    try:
        if path.stat().st_size > 16384:
            raise ValueError("door receipt exceeds 16 KiB")
        with path.open("rb") as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise ValueError("door receipt exceeds 16 KiB")
    except OSError as exc:
        raise ValueError("door receipt unreadable") from exc
    try:
        receipt = json.loads(raw)
        if not isinstance(receipt, dict):
            raise ValueError("door receipt must be an object")
        return _validate_door_receipt(receipt, path, now)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed door receipt") from exc


def _validate_door_receipt(receipt, path, now):
    if receipt.get("schema") != "office-door-shadow/v1":
        raise ValueError("door receipt schema mismatch")
    match = OCCURRENCE.fullmatch(receipt.get("occurrence_id", ""))
    if not match:
        raise ValueError("door occurrence invalid")
    start = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    if Path(path).name != start.strftime("%Y-%m-%dT%H-%MZ.json"):
        raise ValueError("door filename mismatch")
    observed = datetime.fromisoformat(receipt["observed_at"])
    if observed.tzinfo is None or not start <= observed.astimezone(timezone.utc) < start + timedelta(minutes=30):
        raise ValueError("door observed time outside occurrence")
    if observed > now + timedelta(minutes=5):
        raise ValueError("door observation future")
    effect = receipt["effect"]
    if effect.get("classification") not in CLASSES:
        raise ValueError("door classification invalid")
    if effect.get("repair_attempted") is not False or effect.get("muse_owner_retained") is not True:
        raise ValueError("door shadow authority invalid")
    loopback = effect["loopback"]["result"]
    phone = effect["phone"]["result"]
    if not isinstance(loopback.get("ok"), bool) or not isinstance(phone.get("ok"), bool):
        raise ValueError("door check result invalid")
    for result in (loopback, phone):
        if result["ok"] and (not isinstance(result.get("revision"), str)
                             or not result["revision"]):
            raise ValueError("healthy door check needs revision evidence")
    expected = ("door_loopback_failed" if not loopback["ok"] else
                "phone_path_failed" if not phone["ok"] else
                "revision_mismatch" if loopback.get("revision") != phone.get("revision") else
                "healthy")
    if effect["classification"] != expected:
        raise ValueError("door classification disagrees with checks")
    digest = hashlib.sha256(json.dumps(effect, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != receipt.get("effect_sha256"):
        raise ValueError("door effect hash mismatch")
    receipt["late"] = now - observed > timedelta(hours=2)
    return receipt


def ingest_door(ledger, path, now=None):
    """One observation per occurrence; a failed check also opens one fault.

    No repair/escalation is inferred from a probe. A future repair owner must
    explicitly record one bounded attempt and independent verification.
    """
    receipt = read_door_receipt(path, now=now)
    occurrence = receipt["occurrence_id"]
    classification = receipt["effect"]["classification"]
    with ledger.tx():
        previous = ledger.events(kind="watchdog.observed", subject=occurrence)
        if previous:
            old = json.loads(previous[0]["payload"])
            if (old["effect_sha256"] != receipt["effect_sha256"]
                    or old["observed_at"] != receipt["observed_at"]):
                raise ValueError("door occurrence changed after first ingestion")
            return {"occurrence_id": occurrence, "classification": classification,
                    "fault_id": old.get("fault_id"), "duplicate": True}
        observed_ts = datetime.fromisoformat(receipt["observed_at"]).timestamp()
        latest_ts = max((e["ts"] for e in ledger.events(kind="watchdog.observed")
                         if (json.loads(e["payload"]) or {}).get("source") == SOURCE),
                        default=float("-inf"))
        superseded = observed_ts < latest_ts
        fault_id = None
        if not superseded and not receipt["late"] and classification != "healthy":
            fault_id = faults._detect_in_tx(
                ledger, classification, SOURCE, None, observed_ts)
        elif not superseded and not receipt["late"]:
            for rec in faults.records(ledger, state="detected"):
                if rec["source"] == SOURCE:
                    ledger._event("watchdog.recovered", rec["fault_id"],
                                  {"surface": occurrence}, SOURCE, observed_ts)
        ledger._event("watchdog.observed", occurrence,
                      {"effect_sha256": receipt["effect_sha256"],
                       "classification": classification, "fault_id": fault_id,
                       "authority": "shadow", "source": SOURCE,
                       "observed_at": receipt["observed_at"],
                       "late": receipt["late"], "superseded": superseded},
                      SOURCE, observed_ts)
    return {"occurrence_id": occurrence, "classification": classification,
            "fault_id": fault_id, "duplicate": False}
