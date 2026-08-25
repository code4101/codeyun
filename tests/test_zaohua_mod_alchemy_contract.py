from pathlib import Path


ROOT = Path(__file__).parents[1]
SOLVER = ROOT / "tools/zaohua_mod/Code4101.Tiandao/FiniteInventoryAlchemySolver.cs"
WORKER = ROOT / "tools/zaohua_mod/Code4101.Tiandao/AlchemySolveWorker.cs"
UI = ROOT / "tools/zaohua_mod/Code4101.Tiandao/SmartAlchemyFeature.cs"


def test_iterative_solution_does_not_expand_zero_sum_herb_pairs() -> None:
    source = SOLVER.read_text(encoding="utf-8")

    assert "BuildRuleRelevantNeutralPairs" not in source
    assert "ExpandNeutral" not in source
    assert "NeutralPairDepthLimit" not in source
    assert "GenerateExactCompositions" in source


def test_backpack_solution_is_an_inventory_repair_of_the_ideal_solution() -> None:
    solver = SOLVER.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")

    assert "AlchemySolution idealSolution" in solver
    assert "FindInventoryRepairSolution" in solver
    assert "CompositionDistance" in solver
    assert "if (!hasAvailableStatic)" in worker
    assert "request.Inventory, ideal" in worker


def test_spectrum_prefetches_and_publishes_availability_incrementally() -> None:
    source = UI.read_text(encoding="utf-8")

    assert "SessionPlanStates" in source
    assert "_inventorySolutionCache" not in source
    assert "_staticKeyByRecipe" not in source
    assert "RefreshRecipeViews(recipe, state)" in source
    assert "EnqueueRecipe(recipe.id, false)" in source
    assert "ProcessSolveQueue" in source
    assert "nextRefresh = Time.realtimeSinceStartup + 0.25f" in source
    assert "state.Publish(snapshot, request.InventorySignature, request.Inventory)" in source
    assert "RefreshSpectrumAvailability(recipe.id, state.Solutions)" in source
    assert "card.togReady.SetIsOnWithoutNotify(available)" in source


def test_solution_value_uses_native_prices_and_actual_crafting_time() -> None:
    solver = SOLVER.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")

    assert "ResolveOutputItem" in solver
    assert "data.GetItemCfg(pair.Key)?.price" in solver
    assert "recipe.GetCostDay + globalDayBonus + RuleOutcome.DayBonus" in solver
    assert "globalDayMultiplier + RuleOutcome.DayMultiplierBonus / 100f" in solver
    assert "Math.Max(1, craftingDays)" in ui
    assert "output.price * pillCount" in ui
    assert 'field.text = $"日收益 {CompactNumberDisplay.Format(roundedDailyProfit)}"' in ui
    assert "MidpointRounding.AwayFromZero" in ui
    assert "FormatDailyProfit" not in ui
    assert "SetHeightPreservingTop(attrRect" in ui
    assert "previousRect.TransformPoint" in ui
    assert "cardRect.rect.yMax - contentBottom + bottomPadding" in ui


def test_rule_relevant_herbs_are_packed_before_balance_only_herbs() -> None:
    source = SOLVER.read_text(encoding="utf-8")

    assert "CalculateCandidateRulePriority" in source
    assert ".OrderByDescending(candidate => optimizeRules" in source
    assert "TargetMayMatch(candidate.Stock.ItemCfg, state.target1)" in source
    assert "TargetMayMatch(candidate.Stock.ItemCfg, state.target2)" in source


def test_base_layout_uses_rules_and_iterative_search_joins_element_pareto_frontiers() -> None:
    source = SOLVER.read_text(encoding="utf-8")
    canonical = source[source.index("private static RuleSearchResult FindCanonicalSolution") :
                       source.index("private static Composition BuildFastCanonicalComposition")]

    assert "TryPack(fastComposition.Pieces, furnace, recipe, true" in canonical
    assert "TryPack(composition.Pieces, furnace, recipe, true" in canonical
    assert "BuildJointRuleCompositions" in source
    assert "SolveParetoElementAllocations" in source
    assert "ParetoAllocationStateLimit = 50000" in source
    assert "ElementParetoOptionLimit = 24" in source
    assert "JointCompositionBeamLimit = 96" in source
    assert "JointCompositionPackingLimit = 32" in source
    assert "BuildCandidateRuleFeatureKey" in source
    assert "candidate.NextCandidateIndex" not in source
    assert "candidateIndex = state.NextCandidateIndex" in source
    assert "featureSignature" in source
    assert "EstimateCompositionRuleBenefit" in source
    assert "IsBetterRuleSolution(solution, incumbent)" in source
    assert "GenerateExactCompositions(monotoneCandidates" not in source


def test_iterative_search_keeps_the_baseline_path_and_validates_joint_plans_by_real_packing() -> None:
    source = SOLVER.read_text(encoding="utf-8")

    assert "baselineOption" in source
    assert "options.All(option => option.Key != baselineOption.Key)" in source
    assert "baselinePrefix" in source
    assert "beam.All(composition => composition.Key != baselinePrefix.Key)" in source
    assert "TryPack(composition.Pieces, furnace, recipe, true, globalQualityBonus" in source
    assert "alchemy joint iterative recipe=" in source


def test_independent_count_rules_stop_packing_at_theoretical_upper_bound() -> None:
    solver = SOLVER.read_text(encoding="utf-8")
    evaluator = (ROOT / "tools/zaohua_mod/Code4101.Tiandao/AlchemyRuleEvaluator.cs").read_text(
        encoding="utf-8"
    )

    assert "TryCalculateIndependentRuleUpperBound" in solver
    assert "state.relation != 0 || state.stateType == 0" in solver
    assert "CalculateEffectiveRuleBenefit(bestOutcome, globalQualityBonus) >= simpleUpperBenefit" in solver
    assert "if (PackAt(pieceIndex + 1)) return true" in solver
    assert "internal static void ApplyMeasuredState" in evaluator
    assert "internal static int CountAreaCells" in evaluator
    assert "Math.Min(matchingCount, areaCapacity)" in solver


def test_quality_rule_score_stops_at_the_final_quality_cap() -> None:
    solver = SOLVER.read_text(encoding="utf-8")

    assert "CalculateEffectiveRuleBenefit" in solver
    assert "1 + globalQualityBonus + outcome.QualityBonus" in solver
    assert "outcome.Score <= baseline.RuleOutcome.Score" not in solver
    assert "CalculateEffectiveRuleBenefit(candidateOutcome, globalQualityBonus)" in solver
    assert "HasOnlyQualityRuleEffects" in solver
    assert "baseline.QualityRank >= 3" in solver
    assert "incumbent.QualityRank >= 3" in solver


def test_backpack_solution_is_hidden_and_cleared_when_static_solution_is_available() -> None:
    ui = UI.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")

    assert "if (!hasAvailableStatic)" in worker
    assert "private bool HasAvailableStatic()" in ui
    assert "BackpackSolution = null" in ui
    assert "BackpackSolution != null && !HasAvailableStatic()" in ui
    assert "BackpackSolution = HasAvailableStatic()" in ui


def test_base_solution_search_does_not_truncate_before_low_grade_multiplicity() -> None:
    solver = SOLVER.read_text(encoding="utf-8")

    assert "for (var count = maximum; count >= 1; count--)" in solver
    assert "results.Count >= ElementOptionLimit * 4" not in solver
    assert "results.Count > ElementOptionLimit * 8" in solver
    assert ".Take(ElementOptionLimit * 4).ToList()" in solver
    assert "CompareGradeProfiles" in solver
    assert "left.Pieces.Count(item => item.GradeWeight == grade)" in solver
    assert ".OrderBy(item => item, gradeProfileComparer)" in solver
    assert ".ThenBy(item => item.GradeSum)" not in solver


def test_low_grade_canonical_solution_has_a_bounded_fast_path() -> None:
    solver = SOLVER.read_text(encoding="utf-8")

    assert "FastElementSearchNodeLimit = 2048" in solver
    assert "BuildFastCanonicalComposition" in solver
    assert "FindFirstExactElementOption" in solver
    assert "if (fastComposition != null" in solver
    assert "targets.Keys.Any(element => !coveredElements.Contains(element))" in solver
    assert "alchemy static recipe=" in solver


def test_spectrum_replays_shared_state_when_it_becomes_visible() -> None:
    ui = UI.read_text(encoding="utf-8")

    show_spectrum = ui[ui.index("private void ShowSpectrum()") : ui.index("private void CreateSmartPanel()")]
    assert "RefreshStaticAvailabilityAndQueueFallbacks();" in show_spectrum
    assert "卡片可能在后台结果发布后才展开" in ui
    assert "if (_spectrumPanel.activeSelf) RefreshStaticAvailabilityAndQueueFallbacks();" in ui
    assert "spectrum availability recipe=" in ui


def test_completed_static_search_is_reused_even_when_it_found_no_solution() -> None:
    worker = WORKER.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")

    assert "internal bool StaticComplete" in worker
    assert "if (!request.StaticComplete)" in worker
    assert "if (staticSolutions.Count == 0)" not in worker
    assert "StaticComplete = state.StaticComplete" in ui
    assert "if (cachedState.InventoryComplete)" in ui
    assert "if (state.InventoryComplete || state.Solutions.Count > 0)" in ui
    assert "staticComplete={request.StaticComplete}" in ui
