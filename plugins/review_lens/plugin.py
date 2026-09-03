"""First-party Review Lens picker action. A byte-faithful sibling of
plugins/gitlink/plugin.py's own migration: same command_type-string/
parent-validation/record_command/return-id shape via backend/
plugin_sdk.py's make_simple_child_node_handler - see that factory's own
docstring for the shared validate/record_command/return-id contract this
replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginCodeReview",
    warning_suffix="a Review Lens node",
    create=lambda document, parent_node_id: document.add_code_review_node(
        *document.place_child(parent_node_id, "code_review"), parent_node_id
    ),
    # Creatable with nothing selected: this kind never reads the parent's
    # content, so the parent was only ever a place_child anchor and an edge.
    create_standalone=lambda document, x, y: document.add_code_review_node(x, y, None),
)


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Review Lens",
        description=(
            "Fetches a GitHub pull-request diff, walks through it group by "
            "group, and surfaces severity-tiered findings with a "
            "deterministic scorecard."
        ),
        category="Validation & Delivery",
        handler=_execute,
        requires_parent=False,
    )
