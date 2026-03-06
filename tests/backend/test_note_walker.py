import uuid

from backend.core.note_walker import NoteGraphContext, NoteWalker
from backend.models import NoteEdge, NoteNode


def make_note(
    note_id: str,
    title: str,
    *,
    node_status: str = "idea",
    node_type: str = "note",
    weight: int = 100,
    start_at: float = 100.0,
    updated_at: float = 100.0,
    custom_fields=None,
) -> NoteNode:
    return NoteNode(
        id=note_id,
        user_id=1,
        title=title,
        content="",
        weight=weight,
        node_type=node_type,
        node_status=node_status,
        custom_fields=custom_fields or {},
        created_at=start_at,
        updated_at=updated_at,
        start_at=start_at,
        history=[],
    )


def make_edge(source_id: str, target_id: str) -> NoteEdge:
    return NoteEdge(
        id=str(uuid.uuid4()),
        user_id=1,
        source_id=source_id,
        target_id=target_id,
        created_at=100.0,
    )


def build_context(notes, edges=None) -> NoteGraphContext:
    return NoteGraphContext.from_items(notes, edges or [])


def test_collect_all_supports_ordered_exclude_then_reinclude():
    context = build_context(
        [
            make_note("note-open", "Open"),
            make_note("note-done-a", "Done A", node_status="done"),
            make_note("note-done-b", "Done B", node_status="done"),
        ]
    )

    walker = NoteWalker(context, select=False)
    walker.include.all()
    walker.exclude.match_field("node_status", "eq", value="done")
    walker.include.match_id("note-done-b")

    result = walker.collect_all()

    assert result.node_ids == ["note-open", "note-done-b"]


def test_collect_graph_can_expand_through_unselected_nodes():
    context = build_context(
        [
            make_note("root", "Root"),
            make_note("bridge", "Bridge"),
            make_note("leaf", "Leaf"),
        ],
        [
            make_edge("root", "bridge"),
            make_edge("bridge", "leaf"),
        ],
    )

    walker = NoteWalker(context, expand=False, select=False)
    walker.expand.all()
    walker.include_seed.all()
    walker.include.match_id("leaf")

    result = walker.collect_graph(["root"], include_edges=True)

    assert result.node_ids == ["root", "leaf"]
    assert result.edge_ids == []


def test_collect_graph_expand_rules_can_flip_back_to_true():
    context = build_context(
        [
            make_note("root", "Root"),
            make_note("bridge", "Bridge"),
            make_note("leaf", "Leaf"),
            make_note("side", "Side"),
        ],
        [
            make_edge("root", "bridge"),
            make_edge("bridge", "leaf"),
            make_edge("root", "side"),
        ],
    )

    walker = NoteWalker(context, expand=False, select=True)
    walker.expand.all()
    walker.skip.match_id("bridge")
    walker.expand.match_id("bridge")

    result = walker.collect_graph(["root"], include_edges=False)

    assert result.node_ids == ["root", "bridge", "side", "leaf"]


def test_include_seed_applies_base_condition_before_predicate():
    context = build_context(
        [
            make_note("root", "Task"),
            make_note("child", "Task child"),
        ],
        [make_edge("root", "child")],
    )

    walker = NoteWalker(context, expand=True, select=False)
    walker.include_seed.match_title("task")

    result = walker.collect_graph(["root"], include_edges=False)

    assert result.node_ids == ["root"]
