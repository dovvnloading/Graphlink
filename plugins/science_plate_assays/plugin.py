"""Grant-gated Biology Workbench plate-assay pack."""

from graphlink_plugins.scientific.workbench import register_pack


def register(host):
    register_pack(host, "plate_assays")
