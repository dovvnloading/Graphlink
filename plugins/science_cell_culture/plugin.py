"""Grant-gated Biology Workbench cell-culture pack."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, "cell_culture")
