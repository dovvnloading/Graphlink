"""Grant-gated Genetics pack registration for the scientific workbench."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, host.plugin_id.removeprefix("science_"))
