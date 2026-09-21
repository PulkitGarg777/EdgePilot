"""Independent skill checks for both plan installation and serialized dispatch."""

from dataclasses import replace

from .contracts import Action, HOME, Plan, Reason, Skill, Snapshot, Validation


def validate_action(action: Action, state: Snapshot) -> Validation:
    if state.position is None:
        return Validation(False, Reason.MISSING_STATE, "Robot position is unknown")
    if action.skill in (Skill.NAVIGATE, Skill.INSPECT):
        target = next((s for s in state.stations if s.name == action.target), None)
        if target is None:
            return Validation(False, Reason.UNKNOWN_TARGET, str(action.target))
        if target.available is None:
            return Validation(False, Reason.MISSING_STATE, "Target availability is unknown")
        if not target.available:
            return Validation(False, Reason.PRECONDITION, "Target is unavailable")
        if action.skill == Skill.INSPECT and state.position.distance(target.position) > 1e-9:
            return Validation(False, Reason.PRECONDITION, "Inspection requires arrival at target")
    return Validation(True, Reason.OK)


def validate_plan(plan: Plan, state: Snapshot, expected_request_id: str | None,
                  expected_snapshot_id: int | None = None) -> Validation:
    if plan.request_id != expected_request_id:
        return Validation(False, Reason.STALE_REQUEST, "Request is no longer expected")
    if plan.goal_version != state.mission.version:
        return Validation(False, Reason.STALE_GOAL, "Goal has been replaced")
    if plan.snapshot_id > state.snapshot_id:
        return Validation(False, Reason.FUTURE_SNAPSHOT, "Snapshot has not been observed")
    if expected_snapshot_id is not None and plan.snapshot_id != expected_snapshot_id:
        return Validation(False, Reason.SNAPSHOT_MISMATCH, "Snapshot differs from the request")
    if state.active_skill is not None:
        return Validation(False, Reason.BUSY, "Cannot replace an executing plan")
    projected = state
    # Check sequential preconditions against the CURRENT state, projected through
    # preceding actions. The actual executor repeats checks before each dispatch.
    for action in plan.actions:
        check = validate_action(action, projected)
        if not check.accepted:
            return check
        if action.skill == Skill.NAVIGATE:
            target = next(s for s in state.stations if s.name == action.target)
            projected = replace(projected, position=target.position)
        elif action.skill == Skill.HOME:
            projected = replace(projected, position=HOME)
    return Validation(True, Reason.OK)
