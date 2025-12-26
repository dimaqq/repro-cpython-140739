#!/usr/bin/env python3
# Copyright 2025 dima.tisnek@canonical.com
# See LICENSE file for licensing details.
"""Juju's Game of Life."""

if __name__ == "__main__":
    # needs an extra handler to spit info out
    # logging.basicConfig(level="INFO")
    ops.main(JGOLPeerCharm)  # type: ignore
