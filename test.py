"""Test it."""
import json
import logging
import re
from pathlib import Path
from types import MappingProxyType
from typing import cast, Mapping, NewType, cast

import ops
import ops.testing
import pytest
from ops.testing import Context, PeerRelation, State

INIT = "0001110001010101111110001110010101010101001010101000111101010111" * 99


class JGOLPeerCharm(ops.CharmBase):
    """Juju's Game of Life."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.collect_unit_status, self.cell)
        framework.observe(self.on.collect_app_status, self.god)

    def cell(self, _event: ops.EventBase):
        """Update this cell based on neighbours.

        Game of Life operates in lock-step.
        We decompose individual cell logic as follows:
            app = {"round": 42}
            n1 = {"42": 0}
            ...          ------->
            me = {"42": 1, "43": 0}

        * delete any outdated rounds
        * keep current round as is
        * post next round when all inputs are ready
        """
        try:
            world = self.model.get_relation("world")
            assert world, "waiting for peer relation"
            run: bool = json.loads(world.data[self.app]["run"])
            round_: int = json.loads(world.data[self.app]["round"])
            neighbours: dict[str, list[str]] = json.loads(world.data[self.app]["map"])
            init = INIT[: len(neighbours)]
            if self.unit.name not in neighbours:
                self.unit.status = ops.ActiveStatus("unused")
                return
            Path("leader.txt").write_text(world.data[self.app]["leader"])
            Path("neighbours.txt").write_text(" ".join(neighbours[self.unit.name]))
            own_index = list(neighbours).index(self.unit.name)
            init_live = int(init[own_index])

            if not run:
                for k in list(world.data[self.unit]):
                    del world.data[self.unit][k]
                world.data[self.unit][str(round_)] = json.dumps(init_live)
                self.unit.status = ops.ActiveStatus()
                return

            live = json.loads(world.data[self.unit][str(round_)])
            neighbours_alive = sum(
                json.loads(world.data[self.model.get_unit(n)][str(round_)])
                for n in neighbours[self.unit.name]
            )
            if live and neighbours_alive in (2, 3):
                next_live = 1
            elif live:
                next_live = 0
            elif neighbours_alive == 3:
                next_live = 1
            else:
                next_live = 0
            next_round = round_ + 1
            world.data[self.unit][str(next_round)] = json.dumps(next_live)
            # clean up stale rounds
            for k in list(world.data[self.unit]):
                if k.isdigit() and int(k) not in (round_, next_round):
                    del world.data[self.unit][k]

            self.unit.status = ops.ActiveStatus()
        except Exception as e:
            self.unit.status = ops.WaitingStatus(repr(e))

    def god(self, _event: ops.EventBase):
        """Play God with the cells."""
        if not self.unit.is_leader():
            return

        try:
            world = self.model.get_relation("world")
            assert world, "Waiting for peer relation to come up"
            run = bool(cast(bool | None, self.config.get("run")))

            cells = sorted({unit.name for unit in world.units} | {self.unit.name})
            neighbours = neighbourhood(cells)
            assert len(neighbours) <= len(INIT), "Initial map is too small"
            cells = cells[: len(neighbours)]

            curr_round = json.loads(world.data[self.app].get("round", "0"))
            board, next_round = self.board_state(world, cells)

            world.data[self.app]["run"] = json.dumps(run)
            world.data[self.app]["map"] = json.dumps(neighbours)
            world.data[self.app]["leader"] = self.unit.name
            if not run:
                # Reset the board
                world.data[self.app]["round"] = json.dumps(0)
                if next_round == 0:
                    self.app.status = ops.ActiveStatus(msg := f"Reset [{board}]")
                    logging.warning(msg)
                else:
                    # None -> inconsistent map, wait for some units
                    # int, !=0 -> map is consistent but not reset, wait for all units
                    self.app.status = ops.WaitingStatus(msg := f"Resetting... [{board}]")
                    logging.warning(msg)
                    # FIXME we could compare the map to initial...
            elif next_round is not None:
                # All units completed this step, kick off the next round
                world.data[self.app]["round"] = json.dumps(next_round)
                self.app.status = ops.ActiveStatus(
                    msg := f"{curr_round}: [{board}] --> {next_round}"
                )
                logging.warning(msg)
            else:
                # Still waiting for some units
                self.app.status = ops.ActiveStatus(msg := f"{curr_round}: [{board}]")
                logging.warning(msg)
        except Exception as e:
            self.app.status = ops.BlockedStatus(repr(e))

    def board_state(
        self, world: ops.Relation, cells: list[str]
    ) -> tuple[str, int | None]:
        """Determine if all units have completed the current round.

        Returns current map, e.g. "...00..101...." and the target round.
        """
        active_rounds = set()
        board = ""
        for cell in cells:
            try:
                data = world.data[self.model.get_unit(cell)]
                rounds = [int(k) for k in data if k.isdigit()]
                if rounds:
                    active_rounds.add(max(int(k) for k in data if k.isdigit()))
            except Exception as e:
                raise ValueError(f"{cell}: {e}")

        if not active_rounds:
            return "." * len(cells), None

        # FIXME imprecise, may happen to be current round if no unit computed yet
        next_round = max(active_rounds)
        for cell in cells:
            v = world.data[self.model.get_unit(cell)].get(str(next_round))
            # v could be "1", "0" or missing
            board += v or "."

        completed = len(active_rounds) == 1
        return board, max(active_rounds) if completed else None


def neighbourhood(cells: list[str]) -> dict[str, list[str]]:
    """Compute the map of neighbours {unit/1: [unit/3, unit/4, ...], ...}."""
    # Square N x N map
    N = int(len(cells) ** 0.5)  # noqa: N806
    cells = cells[: N * N]

    rv: dict[int, set[int]] = {i: set() for i in range(len(cells))}
    for index in range(len(cells)):
        mey, mex = divmod(index, N)
        # FIXME this can be more elegant if I used slices
        for y in (-1, 0, 1):
            for x in (-1, 0, 1):
                ny = mey + y
                nx = mex + x
                if ny < 0 or ny >= N:
                    continue
                if nx < 0 or nx >= N:
                    continue
                if (ny, nx) == (mey, mex):
                    continue
                neighbour = ny * N + nx
                rv[index].add(neighbour)

    return {cells[k]: sorted(cells[vv] for vv in v) for k, v in rv.items()}



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

META = {
    "name": "jgol-peer",
    "peers": {"world": {"interface": "jgol"}},
    "config": {"options": {"run": {"type": "boolean", "default": False}}},
}

@pytest.fixture
def board():
    return json.dumps(MAP_3X3)


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
        contexts = [Context(JGOLPeerCharm, meta=META, config=META["config"], app_name="app", unit_id=i) for i in range(units)]

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
