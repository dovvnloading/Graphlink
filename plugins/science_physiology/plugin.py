"""Explicit-grant physiology pack for Biology Workbench."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, "physiology")
