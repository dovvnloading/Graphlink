"""Redacted child run receipts: no inputs, settings or output content."""
import re


def validate_child(raw):
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in ("runId", "workflowId", "revision"):
        value = raw.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            return None
        result[key] = value
    if raw.get("status") not in ("completed", "failed"):
        return None
    result["status"] = raw["status"]
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) > 200:
        return None
    result["steps"] = []
    for step in steps:
        if not isinstance(step, dict):
            return None
        sid, status, fp = step.get("stepId"), step.get("status"), step.get("fingerprint")
        if (not isinstance(sid, str) or not re.fullmatch(r"[0-9a-f]{64}", sid)
                or status not in ("running", "completed", "failed")
                or fp is not None and (not isinstance(fp, str) or not re.fullmatch(r"[0-9a-f]{64}", fp))):
            return None
        result["steps"].append({"stepId": sid, "status": status, "fingerprint": fp})
    return result
