"""Project observed run events onto the topology pinned in the report."""

from copy import deepcopy

from backend.app.fault_injection.catalog import catalog


def execution_path(scenario: dict, journal: list[dict]) -> dict:
    """Use journal append order, never reconstruct injection timing from final Trace."""
    if scenario["route"] != "canonical_planning":
        return {}
    topology = deepcopy(next(t for t in catalog()["topologies"] if t["route"] == scenario["route"]))
    steps = []
    validation_round = 0
    for row in journal:
        kind = row.get("kind")
        if kind == "injection":
            if not row.get("applied") or row.get("target_id") != scenario["target_id"]:
                continue
            event = {"event": "fault.injected", "status": "INJECTED"}
        elif kind == "detection":
            event = {"event": "fault.detected", "status": "ERROR"}
        elif kind == "execution":
            event = dict(row["trace_event"])
        elif kind == "state_transition" and row.get("state") == "SAFE_STOP":
            event = {"event": "workflow.safe_stopped", "status": "SAFE_STOP"}
        else:
            continue

        if event.get("event") == "rule_balance.validation_completed":
            validation_round += 1
            event["validation_round"] = validation_round
            event["phase"] = "revalidation" if validation_round > 1 else "validation"

        if kind in {"injection", "detection"}:
            node = next(
                (n for n in topology["nodes"] if row.get("target_id") in n["target_ids"]), None
            )
        elif event.get("event") == "workflow.safe_stopped":
            node = next((n for n in topology["nodes"] if n["node_id"] == "safe_stop"), None)
        elif event.get("event") == "brief.accepted" or str(event.get("event", "")).endswith(
            ".recovery_decided"
        ):
            node = next((n for n in topology["nodes"] if n["node_id"] == "supervisor"), None)
        elif event.get("event") == "agent.recovery_resolved":
            recovered_node = "critic" if "critic" in str(event.get("agent")) else "planner"
            node = next((n for n in topology["nodes"] if n["node_id"] == recovered_node), None)
        elif event.get("event") == "storage.recovery_resolved":
            node = next((n for n in topology["nodes"] if n["node_id"] == "store"), None)
        else:
            node = next(
                (
                    n
                    for n in topology["nodes"]
                    if event.get("event") in n["observed_events"]
                    or event.get("action") in n["observed_events"]
                ),
                None,
            )
        steps.append(
            {
                **event,
                "sequence": len(steps) + 1,
                "kind": kind,
                "run_id": row.get("run_id"),
                "node_id": node["node_id"] if node else None,
                "target_id": row.get("target_id"),
                "occurrence_id": row.get("occurrence_id"),
                "call_index": row.get("call_index"),
                "error_code": row.get("error_code") or event.get("error_code"),
                "occurred_at": row.get("timestamp"),
                "elapsed_monotonic_seconds": row.get("elapsed_monotonic_seconds"),
            }
        )
    return {"path_schema_version": 1, "path_topology": topology, "execution_path": steps}
