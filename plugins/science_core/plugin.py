"""Graphlink Scientific Core pack."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, "core")
