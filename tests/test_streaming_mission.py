"""Offline checks for the constrained EP-016 command surface."""

import pytest

from edgepilot.streaming_mission import build_parser, validate_args


def parsed(*arguments):
    parser = build_parser()
    args = parser.parse_args(arguments)
    validate_args(parser, args)
    return args


def test_finish_arm_keeps_non_cancelling_defaults():
    args = parsed()
    assert args.arm == "finish"
    assert args.scenario == "nominal"
    assert args.policy == "newest-pending"


def test_cancellation_arm_requires_predeclared_goal_replacement_configuration():
    args = parsed("--arm", "cancel-goal-replacement", "--scenario", "goal-replacement")
    assert args.policy == "newest-pending"
    with pytest.raises(SystemExit):
        parsed("--arm", "cancel-goal-replacement", "--scenario", "nominal")
    with pytest.raises(SystemExit):
        parsed("--arm", "cancel-goal-replacement", "--scenario", "goal-replacement",
               "--policy", "fifo")


def test_zero_request_lifetime_is_rejected():
    with pytest.raises(SystemExit):
        parsed("--request-ttl", "0")
