"""Grant-gated phylogenetics pack registration for Biology Workbench."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, host.plugin_id.removeprefix("science_"))
