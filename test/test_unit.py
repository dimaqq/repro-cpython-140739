"""Test it."""

# import concurrent.futures
import json
import re
from types import MappingProxyType
from typing import Mapping, NewType, cast

import ops
import ops.testing
import pytest
from ops.testing import Context, PeerRelation, State

from charm import JGOLPeerCharm

JSON = NewType("JSON", str)

# 3x3 map:
# --------
# 0 1 2
# 3 4 5
# 6 7 8

MAP_3X3 = {
    "app/0": ["app/1", "app/3", "app/4"],
    "app/1": ["app/0", "app/2", "app/3", "app/4", "app/5"],
    "app/2": ["app/1", "app/4", "app/5"],
    "app/3": ["app/0", "app/1", "app/4", "app/6", "app/7"],
    "app/4": ["app/0", "app/1", "app/2", "app/3", "app/5", "app/6", "app/7", "app/8"],
    "app/5": ["app/1", "app/2", "app/4", "app/7", "app/8"],
    "app/6": ["app/3", "app/4", "app/7"],
    "app/7": ["app/3", "app/4", "app/5", "app/6", "app/8"],
    "app/8": ["app/4", "app/5", "app/7"],
}


@pytest.fixture
def board():
    return json.dumps(MAP_3X3)


def test_boot():
    """Leader with blank config, etc."""
    rel = PeerRelation(endpoint="world", id=1, local_app_data={}, peers_data={})
    ctx = Context(JGOLPeerCharm, app_name="app", unit_id=0)
    state = State(leader=True, relations={rel})
    state = ctx.run(ctx.on.update_status(), state)
    assert state.app_status == ops.WaitingStatus("Resetting... [.]")
    assert state.unit_status == ops.ActiveStatus()


def test_boot_unit():
    """Leader with blank config, etc."""
    rel = PeerRelation(endpoint="world", id=1, local_app_data={}, peers_data={})
    ctx = Context(JGOLPeerCharm, app_name="app", unit_id=1)
    state = State(relations={rel})
    state = ctx.run(ctx.on.update_status(), state)
    assert state.unit_status == ops.WaitingStatus("KeyError('run')")


def test_init_unit(board):
    """Leader with blank config, etc."""
    rel = PeerRelation(
        endpoint="world",
        id=1,
        local_app_data={
            "run": "false",
            "round": "0",
            "init": '"000111000"',
            "map": board,
            "leader": "app/42",
        },
        peers_data={},
    )
    ctx = Context(JGOLPeerCharm, app_name="app", unit_id=1)
    state = State(relations={rel})
    state = ctx.run(ctx.on.update_status(), state)
    assert state.unit_status == ops.ActiveStatus()
    rel = state.get_relation(1)
    assert rel.local_unit_data == {"0": "0"}


def exercise(units=20, rounds=20):
    # About 1.5x faster with Python 3.15t
    # Needs a fix in venv ops re OPERATOR_DISPATCH
    # with concurrent.futures.ThreadPoolExecutor() as executor:
    # Weirdly it's not any faster
    # with concurrent.futures.ProcessPoolExecutor() as executor:
    # Must serialise all data for subinterpreters, can't pass dicts
    # with concurrent.futures.InterpreterPoolExecutor() as executor:
        config = {}
        local_app_data: dict[str, JSON] = {}
        peers_data = {i: cast(dict[str, JSON], {}) for i in range(units)}
        unit_messages = {f"app/{i}": "" for i in range(units)}
        app_message = ""
        rv = []
        contexts = [Context(JGOLPeerCharm, app_name="app", unit_id=i) for i in range(units)]

        def loop():
            nonlocal local_app_data, app_message
            results = list(
                # FIXME next speedup: disable logging
                # or use higher logging level threshold in Scenario
                # executor.map(
                map(
                    step,
                    [f"app/{i}" for i in range(units)],
                    [config] * units,
                    [local_app_data] * units,
                    [peers_data] * units,
                    contexts,
                )
            )
            for unit_id in range(units):
                unit = f"app/{unit_id}"
                app_data, unit_data, app_msg, unit_message = results[unit_id]
                peers_data[unit_id] = unit_data
                unit_messages[unit] = unit_message
                if app_data is not None:
                    local_app_data = app_data
                if app_msg is not None:
                    app_message = app_msg
            rv.append(app_message)

            for con in contexts:
                  con.trace_data.clear()
                  con.juju_log.clear()
                  con.removed_secret_revisions.clear()
                  con.requested_storages.clear()
                  con.unit_status_history
                  con.unit_status_history.clear()
                  con.workload_version_history.clear()
                  con.app_status_history.clear()
                  con.action_logs.clear()

        for i in range(3):
            loop()
            print(app_message)
            rounds -= 1
            if not rounds:
                return rv

        del rv[:]

        config = {"run": True}

        while rounds:
            loop()
            print(app_message)
            rounds -= 1

        # __import__("pdb").set_trace()
        print("THE END")
        print(app_message)
        print(unit_messages)
        return rv


def test_init():
    # 11: 3x3 board with a few spare units
    rv = exercise(11, rounds=3)
    assert rv == ["Resetting... [.........]", "Reset [0........]", "Reset [000111000]"]


def test_run():
    rv = exercise(11, rounds=20)
    boards = [board_from_status(r) for r in rv]
    assert set(boards) == {"000111000", "0........", "010010010"}
    # Make sure they are interleaved
    # '000111000', '0........', '010010010', '0........',
    assert len(set(boards[::4])) == 1
    assert len(set(boards[1::4])) == 1
    assert len(set(boards[2::4])) == 1
    assert len(set(boards[3::4])) == 1
    assert len(set(boards[:4])) == 3


def board_from_status(st: str) -> str | None:
    if match := re.search(r"\[(.*)\]", st):
        return match.groups()[0]


def step(
    unit: str,
    config: Mapping[str, str | int | float | bool],
    local_app_data: Mapping[str, JSON],
    all_units_data: Mapping[ops.testing.UnitID, Mapping[str, JSON]],
    context: ops.testing.Context,
) -> tuple[dict[str, JSON] | None, dict[str, JSON], str | None, str]:
    unit_id = int(unit.split("/")[-1])
    is_leader = not unit_id
    peers_data = {k: v for k, v in all_units_data.items() if k != unit_id}
    local_unit_data = all_units_data[unit_id]
    rel = PeerRelation(
        endpoint="world",
        id=1,
        local_app_data=cast(dict[str, str], local_app_data),
        local_unit_data=cast(dict[str, str], local_unit_data),
        peers_data=cast(dict[ops.testing.UnitID, dict[str, str]], peers_data),
    )
    # https://github.com/canonical/operator/issues/2152
    config_ = cast(dict[str, str | int | float | bool], MappingProxyType(config))
    state = State(relations={rel}, leader=is_leader, config=config_)
    state = context.run(context.on.update_status(), state)
    rel = state.get_relation(1)
    app_data = cast(dict[str, JSON], rel.local_app_data) if is_leader else None
    unit_data = cast(dict[str, JSON], rel.local_unit_data)
    app_message = state.app_status.message if is_leader else None
    unit_message = state.unit_status.message
    return app_data, unit_data, app_message, unit_message


if __name__ == "__main__":
    import sys
    args = map(int, sys.argv[0:])
    exercise(int(sys.argv[1]), int(sys.argv[2]))
