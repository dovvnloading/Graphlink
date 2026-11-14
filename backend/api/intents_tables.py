"""Canvas commands for editable tables and their versioned chart and Drafter outputs."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, TypeVar

from backend.api._shared import make_publish_scene
from backend.domain.geometry_safety import finite_float
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError, SceneNode
from backend.events import IntentValidationError, SessionBus

T = TypeVar("T")


@dataclass
class AddTableArgs:
    x: float
    y: float
    csv: str


@dataclass
class SetTableCsvArgs:
    sourceId: str
    csv: str
    expectedContent: str


@dataclass
class AddTableOutputArgs:
    sourceId: str
    kind: Literal["chart", "artifact"]
    groupColumn: str
    valueColumn: str


@dataclass
class TableOutputArgs:
    targetId: str


@dataclass
class RestoreTableOutputArgs:
    targetId: str
    expectedRevisionToken: str


@dataclass
class TableContributorsArgs:
    targetId: str
    group: str
    offset: int


@contextmanager
def _table_errors() -> Iterator[None]:
    """Pass domain corrections through the WebSocket's validation-error response."""
    try:
        yield
    except SceneError as exc:
        raise IntentValidationError([str(exc).rstrip(".")]) from exc


def register_table_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def edit(command: str, mutate: Callable[[], T], node_ids: tuple[str, ...] = ()) -> T:
        with _table_errors():
            result, _ = document.record_command(command, "user", mutate, node_ids=node_ids)
        await publish_scene()
        return result

    async def add_table(x: float, y: float, csv: str) -> str:
        def create() -> SceneNode:
            width, height = document.kind_fallback_footprint("note")
            nx, ny = document.find_free_position(finite_float(x, "x"), finite_float(y, "y"), width, height)
            return document.add_table(nx, ny, csv)

        node = await edit("addTable", create)
        return node.id

    async def set_table_csv(source_id: str, csv: str, expected_content: str) -> None:
        await edit("setTableCsv", lambda: document.set_table_source(source_id, csv, expected_content), (source_id,))

    async def add_table_output(source_id: str, kind: str, group_column: str, value_column: str) -> str:
        node = await edit(
            "addTableOutput",
            lambda: document.create_table_output(source_id, kind, group_column, value_column),
        )
        return node.id

    async def run_table_output(target_id: str) -> None:
        await edit("runTableOutput", lambda: document.run_table_output(target_id), (target_id,))

    async def restore_table_output(target_id: str, expected_revision_token: str) -> None:
        await edit(
            "restoreTableOutput", lambda: document.restore_table_output(target_id, expected_revision_token), (target_id,),
        )

    async def get_table_output_history(target_id: str):
        with _table_errors():
            return document.get_table_output_history(target_id)

    async def get_table_contributors(target_id: str, group: str, offset: int):
        with _table_errors():
            return document.get_table_contributors(target_id, group, offset)

    bus.register_intent("scene", "addTable", add_table, args_schema=AddTableArgs)
    bus.register_intent("scene", "setTableCsv", set_table_csv, args_schema=SetTableCsvArgs)
    bus.register_intent("scene", "addTableOutput", add_table_output, args_schema=AddTableOutputArgs)
    bus.register_intent("scene", "runTableOutput", run_table_output, args_schema=TableOutputArgs)
    bus.register_intent("scene", "restoreTableOutput", restore_table_output, args_schema=RestoreTableOutputArgs)
    bus.register_intent("scene", "getTableOutputHistory", get_table_output_history, args_schema=TableOutputArgs)
    bus.register_intent("scene", "getTableContributors", get_table_contributors, args_schema=TableContributorsArgs)
