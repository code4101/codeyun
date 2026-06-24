from backend.core.hierarchical_reduction import (
    HierarchicalReductionError,
    ReductionModelResponse,
    ReductionProfile,
    ReductionPrompt,
    ReductionSourceUnit,
    estimate_level_count,
    extract_json_object,
    run_hierarchical_reduction,
)


def _build_test_prompt(chunk, *, profile):
    del profile
    item_ids = ",".join(item.ref_id for item in chunk.items)
    return ReductionPrompt(
        system_prompt="只返回 JSON",
        user_prompt=f"{chunk.input_kind}:{item_ids}",
    )


def _run_test_model(prompt, *, chunk, profile):
    del prompt, profile
    source_refs: list[str] = []
    for item in chunk.items:
        if item.kind == "source":
            source_refs.append(item.ref_id)
            continue
        source_refs.extend(item.metadata.get("source_refs") or [])

    return ReductionModelResponse(
        model="test-model",
        content={
            "summary": f"L{chunk.level}:{'+'.join(source_refs)}",
            "source_count": len(source_refs),
        },
    )


def _finalize_root(root_node, *, levels, profile):
    return {
        "profile_id": profile.profile_id,
        "level_count": len(levels),
        "summary": root_node.payload["summary"],
        "source_count": root_node.payload["source_count"],
        "source_refs": root_node.source_refs,
    }


def test_hierarchical_reduction_builds_multiple_levels_and_root_refs():
    profile = ReductionProfile(
        profile_id="git-commit",
        task_type="git_commit",
        branch_factor=3,
        leaf_prompt_builder=_build_test_prompt,
        reduce_prompt_builder=_build_test_prompt,
        payload_parser=lambda response, **_: extract_json_object(response.content),
        finalizer=_finalize_root,
    )
    source_units = [
        ReductionSourceUnit(unit_id=f"file_{index}", content=f"change {index}")
        for index in range(10)
    ]

    result = run_hierarchical_reduction(
        source_units,
        profile=profile,
        model_runner=_run_test_model,
        root_input_meta={"repo": "demo"},
    )

    assert result.task_type == "git_commit"
    assert result.profile_id == "git-commit"
    assert result.root_input_meta == {"repo": "demo"}
    assert [len(level.nodes) for level in result.levels] == [4, 2, 1]
    assert result.root_node.source_refs == [f"file_{index}" for index in range(10)]
    assert result.final_result["source_count"] == 10
    assert result.final_result["summary"] == "L2:file_0+file_1+file_2+file_3+file_4+file_5+file_6+file_7+file_8+file_9"


def test_extract_json_object_accepts_code_fence_and_embedded_text():
    fenced = """```json
{"summary": "ok", "count": 2}
```"""
    embedded = '模型回答如下：{"summary": "ok", "count": 2}'

    assert extract_json_object(fenced) == {"summary": "ok", "count": 2}
    assert extract_json_object(embedded) == {"summary": "ok", "count": 2}


def test_estimate_level_count_matches_default_branching():
    assert estimate_level_count(0, branch_factor=10) == 0
    assert estimate_level_count(1, branch_factor=10) == 1
    assert estimate_level_count(10, branch_factor=3) == 3
    assert estimate_level_count(1000, branch_factor=10) == 3


def test_hierarchical_reduction_wraps_chunk_errors():
    profile = ReductionProfile(
        profile_id="broken-profile",
        task_type="demo",
        branch_factor=4,
        leaf_prompt_builder=_build_test_prompt,
        reduce_prompt_builder=_build_test_prompt,
        payload_parser=lambda response, **_: extract_json_object(response.content),
    )

    def broken_runner(prompt, *, chunk, profile):
        del prompt, chunk, profile
        return ReductionModelResponse(model="test-model", content="not json")

    try:
        run_hierarchical_reduction(
            [ReductionSourceUnit(unit_id="a", content="alpha")],
            profile=profile,
            model_runner=broken_runner,
        )
    except HierarchicalReductionError as exc:
        assert "chunk broken-profile:L0:C0" in str(exc)
    else:
        raise AssertionError("expected HierarchicalReductionError")


def test_hierarchical_reduction_reports_progress_events():
    profile = ReductionProfile(
        profile_id="progress-demo",
        task_type="demo",
        branch_factor=2,
        leaf_prompt_builder=_build_test_prompt,
        reduce_prompt_builder=_build_test_prompt,
        payload_parser=lambda response, **_: extract_json_object(response.content),
        finalizer=_finalize_root,
    )
    source_units = [
        ReductionSourceUnit(unit_id=f"file_{index}", content=f"change {index}")
        for index in range(5)
    ]
    events = []

    result = run_hierarchical_reduction(
        source_units,
        profile=profile,
        model_runner=_run_test_model,
        progress_callback=events.append,
    )

    assert result.final_result["source_count"] == 5
    assert events[0] == {
        "event": "level_started",
        "level": 0,
        "input_kind": "source",
        "chunk_count": 3,
        "completed_chunk_count": 0,
    }
    chunk_events = [item for item in events if item["event"] == "chunk_completed"]
    assert len(chunk_events) == 6
    assert chunk_events[-1]["completed_chunk_count"] == 6
    assert events[-1] == {
        "event": "completed",
        "level": 2,
        "completed_chunk_count": 6,
        "level_count": 3,
    }
