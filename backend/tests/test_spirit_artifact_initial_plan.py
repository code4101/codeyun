from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_initial_plan import (
    SpiritArtifactInitialFacts as F, plan_spirit_artifact_initial_parts,
)


def test_global_part_order_and_unknown_inventory_not_blocked():
    plan = plan_spirit_artifact_initial_parts([
        F(2,3,'初始',0,0,0,6), F(5,5,'初始',0), F(9,6,'初始',0)])
    assert [(e.assessment.ware_id,e.assessment.part) for e in plan] == [(5,5),(9,6),(2,3)]
    assert plan[0].assessment.readiness == 'needs_analysis'
    assert plan[2].gap.red_grade_units_missing == 1
    assert plan[2].assessment.readiness == 'ready'


def test_initial_requires_one_raw_not_six():
    plan = plan_spirit_artifact_initial_parts([
        F(7,5,'初始',0,0,0,0), F(5,4,'初始',0,0,0,None), F(5,1,'初始',0,1,1,None)])
    assert plan[0].gap.red_grade_units_missing == 1
    assert plan[0].assessment.readiness == 'blocked'
    assert plan[1].assessment.readiness == 'ready'
    assert plan[2].assessment.readiness == 'needs_analysis'
