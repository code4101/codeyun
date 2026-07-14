using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace Code4101.Zaohua.Tiandao
{
    internal static class DantianSyntheticLayoutContext
    {
        [ThreadStatic]
        internal static List<TbDantianSto> Current;
    }

    [HarmonyPatch(typeof(DantianController), nameof(DantianController.GetDantianStoList))]
    internal static class DantianSyntheticLayoutPatch
    {
        private static bool Prefix(ref List<TbDantianSto> __result)
        {
            if (DantianSyntheticLayoutContext.Current == null) return true;
            __result = DantianSyntheticLayoutContext.Current;
            return false;
        }
    }

    internal sealed class DantianLayoutScore
    {
        internal double Balance;
        internal int Total;
        internal int RuleCount;
        internal List<int> Multipliers = new List<int>();

        internal double Fitness => Balance + Total * 0.000001d;

        internal bool BetterThan(DantianLayoutScore other)
        {
            if (other == null) return true;
            if (Balance > other.Balance + 0.0000001d) return true;
            return Math.Abs(Balance - other.Balance) <= 0.0000001d && Total > other.Total;
        }

        public override string ToString()
        {
            var geometric = RuleCount == 0
                ? 0d
                : Math.Exp(Balance / RuleCount) - 1d;
            return $"balanced={geometric:F3}, total={Total}, rules=[{string.Join(",", Multipliers)}]";
        }
    }

    internal sealed class DantianLayoutPlacement
    {
        internal int OriginId;
        internal int X;
        internal int Y;
        internal int Rotation;
        internal int[] CellIndices;
    }

    internal sealed class DantianLayoutPiece
    {
        internal BlendId Id;
        internal string Name;
        internal int QuickKey;
        internal int CurrentOriginId;
        internal int CurrentRotation;
        internal TbDrawCfg Draw;
        internal List<TbDrawStateCfg> Rules = new List<TbDrawStateCfg>();
        internal List<DantianLayoutPlacement> Placements = new List<DantianLayoutPlacement>();
        internal int CurrentPlacementIndex = -1;
    }

    internal sealed class DantianLayoutProblem
    {
        internal List<TbDantianSto> Board;
        internal Dictionary<int, int> CellIndexById;
        internal int[][] NeighborIndices;
        internal List<DantianLayoutPiece> Pieces;
        internal int[] CurrentPlacements;

        internal List<TbDantianSto> Materialize(int[] placementIndices)
        {
            var layout = Board.Select(cell => new TbDantianSto
            {
                id = cell.id,
                npcStoId = cell.npcStoId,
                x = cell.x,
                y = cell.y,
            }).ToList();
            for (var pieceIndex = 0; pieceIndex < Pieces.Count; pieceIndex++)
            {
                var piece = Pieces[pieceIndex];
                var placement = piece.Placements[placementIndices[pieceIndex]];
                foreach (var cellIndex in placement.CellIndices)
                    layout[cellIndex].artMagicId = piece.Id;
            }
            return layout;
        }
    }

    internal static class DantianLayoutOptimizer
    {
        private static readonly System.Reflection.MethodInfo GetUpMultiplierMethod =
            AccessTools.Method(typeof(DantianController), "GetUpMultiplier", new[]
            {
                typeof(List<TbDantianSto>), typeof(string), typeof(string), typeof(int),
            });

        internal static bool TryCapture(out DantianLayoutProblem problem, out string error)
        {
            problem = null;
            error = null;
            var actor = BsSaveDataImpl.nowActor;
            var controller = Singleton<DantianController>.Instance;
            var board = controller?.GetDantianStoList()?.Where(cell => cell != null).ToList();
            if (actor == null || controller == null || board == null || board.Count == 0)
            {
                error = "丹田数据尚未就绪";
                return false;
            }

            var byId = board.Select((cell, index) => new { cell.id, index })
                .ToDictionary(item => item.id, item => item.index);
            var occupiedGroups = board
                .Where(cell => cell.artMagicId.sedId != 0)
                .GroupBy(cell => BlendKey(cell.artMagicId))
                .ToList();
            if (occupiedGroups.Count == 0)
            {
                error = "丹田中还没有已选择的功法或术法";
                return false;
            }

            var pieces = new List<DantianLayoutPiece>();
            foreach (var group in occupiedGroups)
            {
                var id = group.First().artMagicId;
                if (!TryCreatePiece(actor, id, out var piece))
                {
                    error = $"无法读取已放置对象 BlendId({(int)id.blendEnum},{id.sedId})";
                    return false;
                }
                var occupied = new HashSet<int>(group.Select(cell => byId[cell.id]));
                BuildPlacements(controller, board, byId, piece);
                piece.CurrentPlacementIndex = piece.Placements.FindIndex(placement =>
                    placement.Rotation == piece.CurrentRotation &&
                    placement.OriginId == piece.CurrentOriginId &&
                    occupied.SetEquals(placement.CellIndices));
                if (piece.CurrentPlacementIndex < 0)
                    piece.CurrentPlacementIndex = piece.Placements.FindIndex(placement =>
                        occupied.SetEquals(placement.CellIndices));
                if (piece.CurrentPlacementIndex < 0)
                {
                    error = $"当前图形无法还原：{piece.Name}";
                    return false;
                }
                pieces.Add(piece);
            }

            // 大图形和候选少的对象优先，能提高随机移动时的可行率。
            pieces = pieces.OrderByDescending(piece =>
                    piece.Placements[piece.CurrentPlacementIndex].CellIndices.Length)
                .ThenBy(piece => piece.Placements.Count)
                .ToList();
            var current = pieces.Select(piece => piece.CurrentPlacementIndex).ToArray();
            problem = new DantianLayoutProblem
            {
                Board = board,
                CellIndexById = byId,
                NeighborIndices = BuildNeighbors(board),
                Pieces = pieces,
                CurrentPlacements = current,
            };
            return true;
        }

        internal static DantianLayoutScore Evaluate(DantianLayoutProblem problem,
            int[] placementIndices)
        {
            var layout = problem.Materialize(placementIndices);
            var score = new DantianLayoutScore();
            var controller = Singleton<DantianController>.Instance;
            DantianSyntheticLayoutContext.Current = layout;
            try
            {
                for (var pieceIndex = 0; pieceIndex < problem.Pieces.Count; pieceIndex++)
                {
                    var piece = problem.Pieces[pieceIndex];
                    if (piece.Rules.Count == 0) continue;
                    var source = piece.Placements[placementIndices[pieceIndex]].CellIndices
                        .Select(index => layout[index])
                        .ToList();
                    foreach (var rule in piece.Rules)
                    {
                        var targets = controller.GetVaildArtMagicIdList(source, layout, rule.targetEff);
                        var hasTarget = targets != null && targets.Any(cell => cell.artMagicId.sedId != 0);
                        var multiplier = hasTarget && GetUpMultiplierMethod != null
                            ? (int)GetUpMultiplierMethod.Invoke(controller, new object[]
                            {
                                source, rule.upMulEff, rule.upMulType, rule.maxUpMul,
                            })
                            : 0;
                        multiplier = Math.Max(0, multiplier);
                        score.RuleCount++;
                        score.Total += multiplier;
                        score.Balance += Math.Log(1d + multiplier);
                        score.Multipliers.Add(multiplier);
                    }
                }
            }
            finally
            {
                DantianSyntheticLayoutContext.Current = null;
            }
            return score;
        }

        internal static bool TryEvaluate(DantianLayoutProblem problem, int[] placementIndices,
            out DantianLayoutScore score, out string error)
        {
            try
            {
                score = Evaluate(problem, placementIndices);
                error = null;
                return true;
            }
            catch (Exception exception)
            {
                DantianSyntheticLayoutContext.Current = null;
                score = null;
                error = $"{exception.GetType().Name}: {exception.Message}";
                return false;
            }
        }

        internal static bool Apply(DantianLayoutProblem problem, int[] placementIndices,
            out string error)
        {
            error = null;
            var controller = Singleton<DantianController>.Instance;
            if (controller == null)
            {
                error = "丹田控制器不可用";
                return false;
            }
            var original = problem.CurrentPlacements.ToArray();
            try
            {
                if (TryApply(controller, problem, placementIndices, out error)) return true;
            }
            catch (Exception exception)
            {
                error = $"{exception.GetType().Name}: {exception.Message}";
            }

            var applyError = error;
            try
            {
                if (!TryApply(controller, problem, original, out var rollbackError))
                    error = $"{applyError}；恢复原布局也失败：{rollbackError}";
                else
                    error = $"{applyError}；已恢复原布局";
            }
            catch (Exception rollbackException)
            {
                error = $"{applyError}；恢复原布局异常：{rollbackException.Message}";
            }
            return false;
        }

        private static bool TryApply(DantianController controller, DantianLayoutProblem problem,
            int[] placements, out string error)
        {
            error = null;
            foreach (var piece in problem.Pieces)
                controller.RemoveArtMagic(piece.Id, false);

            for (var i = 0; i < problem.Pieces.Count; i++)
            {
                var piece = problem.Pieces[i];
                var placement = piece.Placements[placements[i]];
                var refresh = i == problem.Pieces.Count - 1;
                if (controller.PlaceArtMagicOnDantian(piece.Id, placement.X, placement.Y,
                        placement.Rotation, refresh, piece.QuickKey))
                    continue;
                error = $"放置失败：{piece.Name} @({placement.X},{placement.Y})/{placement.Rotation}";
                return false;
            }
            if (!MatchesCurrentLayout(controller, problem, placements))
            {
                error = "宿主返回放置成功，但最终丹田格位与目标解不一致";
                return false;
            }
            return true;
        }

        private static bool MatchesCurrentLayout(DantianController controller,
            DantianLayoutProblem problem, int[] placements)
        {
            var expected = problem.Materialize(placements)
                .ToDictionary(cell => cell.id, cell => cell.artMagicId);
            var actual = controller.GetDantianStoList();
            return actual != null && actual.Count == expected.Count && actual.All(cell =>
                expected.TryGetValue(cell.id, out var expectedId) && Same(cell.artMagicId, expectedId));
        }

        private static bool TryCreatePiece(TbActor actor, BlendId id,
            out DantianLayoutPiece piece)
        {
            piece = null;
            var drawId = 0;
            var drawStateIds = string.Empty;
            var name = $"BlendId({(int)id.blendEnum},{id.sedId})";
            var quickKey = 0;
            var originId = 0;
            var rotation = 0;

            if (TbArtImpl.IsArt(id))
            {
                var sto = actor.artStoList.FirstOrDefault(item => item.flag > 0 && Same(item.artId, id));
                var cfg = Singleton<TbArtImpl>.Instance.GetArtCfg(id);
                if (sto == null || cfg == null) return false;
                drawId = cfg.drawId;
                drawStateIds = cfg.drawStateId;
                name = cfg.GetName;
                quickKey = sto.flag;
                originId = sto.dantianStoId;
                rotation = sto.rotate;
            }
            else if (TbMagicImpl.IsMagic(id))
            {
                var sto = actor.magicStoList.FirstOrDefault(item => item.flag > 0 && Same(item.magicId, id));
                var cfg = Singleton<TbMagicImpl>.Instance.GetMagicCfg(id);
                if (sto == null || cfg == null) return false;
                drawId = cfg.drawId;
                drawStateIds = cfg.drawStateId;
                name = cfg.GetName;
                quickKey = sto.flag;
                originId = sto.dantianStoId;
                rotation = sto.rotate;
            }
            else if (TbSkillImpl.IsSkill(id))
            {
                var sto = actor.skillStoList.FirstOrDefault(item => item.flag > 0 && Same(item.skillId, id));
                var cfg = Singleton<TbSkillImpl>.Instance.GetSkillCfg(id);
                if (sto == null || cfg == null) return false;
                drawId = cfg.drawId;
                drawStateIds = cfg.drawStateId;
                name = cfg.GetName;
                quickKey = sto.flag;
                originId = sto.dantianStoId;
                rotation = sto.rotate;
            }
            else
            {
                return false;
            }

            var draw = Singleton<TbDataImpl>.Instance.GetDrawCfg(drawId);
            if (draw == null || draw.Coordinates == null || draw.Coordinates.Count == 0) return false;
            piece = new DantianLayoutPiece
            {
                Id = id,
                Name = name,
                QuickKey = quickKey,
                CurrentOriginId = originId,
                CurrentRotation = rotation,
                Draw = draw,
            };
            foreach (var raw in (drawStateIds ?? string.Empty).Split('&'))
            {
                if (!int.TryParse(raw, out var ruleId)) continue;
                var rule = Singleton<TbDataImpl>.Instance.GetDrawStateCfg(ruleId);
                if (rule != null) piece.Rules.Add(rule);
            }
            return true;
        }

        private static void BuildPlacements(DantianController controller,
            List<TbDantianSto> board, Dictionary<int, int> byId, DantianLayoutPiece piece)
        {
            var unique = new HashSet<string>();
            for (var rotation = 0; rotation < 4; rotation++)
            {
                foreach (var origin in board)
                {
                    var cells = controller.GetDantianStoListByArtMagic(origin, rotation, piece.Draw);
                    if (cells == null || cells.Count != piece.Draw.Coordinates.Count) continue;
                    var indices = cells.Select(cell => byId[cell.id]).Distinct().OrderBy(index => index).ToArray();
                    if (indices.Length != piece.Draw.Coordinates.Count) continue;
                    var signature = string.Join(",", indices);
                    if (!unique.Add(signature)) continue;
                    piece.Placements.Add(new DantianLayoutPlacement
                    {
                        OriginId = origin.id,
                        X = origin.x,
                        Y = origin.y,
                        Rotation = rotation,
                        CellIndices = indices,
                    });
                }
            }
        }

        private static int[][] BuildNeighbors(List<TbDantianSto> board)
        {
            var byCoordinate = board.Select((cell, index) => new { cell, index })
                .ToDictionary(item => $"{item.cell.x}:{item.cell.y}", item => item.index);
            return board.Select(cell => new[]
                {
                    $"{cell.x + 1}:{cell.y}", $"{cell.x - 1}:{cell.y}",
                    $"{cell.x}:{cell.y + 1}", $"{cell.x}:{cell.y - 1}",
                }
                .Where(byCoordinate.ContainsKey)
                .Select(key => byCoordinate[key])
                .ToArray()).ToArray();
        }

        internal static bool Same(BlendId left, BlendId right)
        {
            return left.blendEnum == right.blendEnum && left.sedId == right.sedId;
        }

        private static string BlendKey(BlendId id)
        {
            return $"{(int)id.blendEnum}:{id.sedId}";
        }
    }

    internal sealed class DantianOptimizerUi : MonoBehaviour
    {
        private const int MaxIterations = 18000;
        private const int MaxMilliseconds = 1600;
        private DantianPanel _panel;
        private Button _button;
        private TextPro _label;
        private Coroutine _running;
        private int _runId;

        internal void Initialize(DantianPanel panel)
        {
            if (_panel != null) return;
            _panel = panel;
            var view = Traverse.Create(panel).Field<DantianPanelView>("view").Value;
            if (view == null) return;
            var reset = view.DantianOperationArea.btnResetDantian;
            if (reset == null) return;
            _button = Instantiate(reset, reset.transform.parent);
            _button.gameObject.name = "Code4101DantianOptimize";
            _button.onClick.RemoveAllListeners();
            foreach (var localization in _button.GetComponentsInChildren<TextProLocalization>(true))
                localization.enabled = false;
            var labels = _button.GetComponentsInChildren<TextPro>(true);
            _label = labels.FirstOrDefault();
            if (_label != null) _label.text = "优化排布";
            for (var i = 1; i < labels.Length; i++) labels[i].text = string.Empty;

            var resetRect = (RectTransform)reset.transform;
            var rect = (RectTransform)_button.transform;
            rect.anchorMin = resetRect.anchorMin;
            rect.anchorMax = resetRect.anchorMax;
            rect.pivot = resetRect.pivot;
            rect.sizeDelta = resetRect.sizeDelta;
            var parentLayout = reset.transform.parent.GetComponent<LayoutGroup>();
            if (parentLayout != null)
                _button.transform.SetSiblingIndex(reset.transform.GetSiblingIndex() + 1);
            else
                rect.localPosition = resetRect.localPosition +
                                     new Vector3(resetRect.rect.width + 10f, 0f, 0f);
            _button.onClick.AddListener(StartOptimization);
        }

        private void StartOptimization()
        {
            if (_running != null) return;
            _running = StartCoroutine(OptimizeAndApply(++_runId));
        }

        private IEnumerator OptimizeAndApply(int runId)
        {
            _button.interactable = false;
            SetLabel("分析中…");
            if (!DantianLayoutOptimizer.TryCapture(out var problem, out var captureError))
            {
                UnityEngine.Debug.LogWarning($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] capture-failed {captureError}");
                yield return ShowTemporary(captureError);
                yield break;
            }

            var rules = problem.Pieces.Sum(piece => piece.Rules.Count);
            var candidates = problem.Pieces.Sum(piece => piece.Placements.Count);
            var current = problem.CurrentPlacements.ToArray();
            if (!DantianLayoutOptimizer.TryEvaluate(problem, current, out var currentScore,
                    out var scoreError))
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] score-failed {scoreError}");
                yield return ShowTemporary("评分失败");
                yield break;
            }
            var best = current.ToArray();
            var bestScore = currentScore;
            var working = current.ToArray();
            var workingScore = currentScore;
            var occupancy = BuildOccupancy(problem, working);
            var random = new System.Random(unchecked(BsSaveDataImpl.nowActor.randomSeed + runId * 7919));
            var totalWatch = Stopwatch.StartNew();
            var sliceWatch = Stopwatch.StartNew();
            var accepted = 0;

            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] begin " +
                                  $"board={problem.Board.Count} pieces={problem.Pieces.Count} rules={rules} " +
                                  $"placements={candidates} current={currentScore} " +
                                  $"runtime=[{DescribeRuntimeBonuses()}]");

            for (var iteration = 0;
                 iteration < MaxIterations && totalWatch.ElapsedMilliseconds < MaxMilliseconds;
                 iteration++)
            {
                var pieceIndex = random.Next(problem.Pieces.Count);
                var piece = problem.Pieces[pieceIndex];
                var oldPlacementIndex = working[pieceIndex];
                var oldPlacement = piece.Placements[oldPlacementIndex];
                foreach (var cell in oldPlacement.CellIndices) occupancy[cell]--;

                var newPlacementIndex = PickPlacement(problem, piece, occupancy, random);
                if (newPlacementIndex < 0 || newPlacementIndex == oldPlacementIndex)
                {
                    foreach (var cell in oldPlacement.CellIndices) occupancy[cell]++;
                }
                else
                {
                    working[pieceIndex] = newPlacementIndex;
                    if (!DantianLayoutOptimizer.TryEvaluate(problem, working, out var nextScore,
                            out scoreError))
                    {
                        working[pieceIndex] = oldPlacementIndex;
                        foreach (var cell in oldPlacement.CellIndices) occupancy[cell]++;
                        UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                                   $"candidate-score-failed {scoreError}");
                        break;
                    }
                    var progress = (double)iteration / MaxIterations;
                    var temperature = 0.22d * (1d - progress) + 0.012d;
                    var delta = nextScore.Fitness - workingScore.Fitness;
                    var accept = delta >= 0d || random.NextDouble() < Math.Exp(delta / temperature);
                    if (accept)
                    {
                        foreach (var cell in piece.Placements[newPlacementIndex].CellIndices)
                            occupancy[cell]++;
                        workingScore = nextScore;
                        accepted++;
                        if (nextScore.BetterThan(bestScore))
                        {
                            best = working.ToArray();
                            bestScore = nextScore;
                        }
                    }
                    else
                    {
                        working[pieceIndex] = oldPlacementIndex;
                        foreach (var cell in oldPlacement.CellIndices) occupancy[cell]++;
                    }
                }

                if (sliceWatch.ElapsedMilliseconds < 5) continue;
                SetLabel("优化中…");
                sliceWatch.Restart();
                yield return null;
                if (_panel == null || !_panel.gameObject.activeInHierarchy) yield break;
            }

            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] searched " +
                                  $"elapsedMs={totalWatch.ElapsedMilliseconds} accepted={accepted} " +
                                  $"best={bestScore} solution=[{DescribeSolution(problem, best)}]");
            if (!bestScore.BetterThan(currentScore))
            {
                yield return ShowTemporary("暂未找到更优");
                yield break;
            }

            SetLabel("应用中…");
            if (!DantianLayoutOptimizer.Apply(problem, best, out var applyError))
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] apply-failed {applyError}");
                yield return ShowTemporary("应用失败");
                yield break;
            }
            DantianLayoutScore actual = null;
            if (DantianLayoutOptimizer.TryCapture(out var actualProblem, out _))
                DantianLayoutOptimizer.TryEvaluate(actualProblem, actualProblem.CurrentPlacements,
                    out actual, out _);
            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] applied " +
                                  $"before={currentScore} predicted={bestScore} actual={actual}");
            Traverse.Create(_panel).Method("InitPanel").GetValue();
            yield return ShowTemporary($"总增幅 {currentScore.Total}→{bestScore.Total}");
        }

        private static int[] BuildOccupancy(DantianLayoutProblem problem, int[] placements)
        {
            var occupancy = new int[problem.Board.Count];
            for (var i = 0; i < problem.Pieces.Count; i++)
                foreach (var cell in problem.Pieces[i].Placements[placements[i]].CellIndices)
                    occupancy[cell]++;
            return occupancy;
        }

        private static int PickPlacement(DantianLayoutProblem problem, DantianLayoutPiece piece,
            int[] occupancy, System.Random random)
        {
            var fallback = -1;
            for (var attempt = 0; attempt < 72; attempt++)
            {
                var index = random.Next(piece.Placements.Count);
                var placement = piece.Placements[index];
                if (placement.CellIndices.Any(cell => occupancy[cell] > 0)) continue;
                fallback = index;
                if (random.NextDouble() > 0.18d && !Touches(problem, placement, occupancy)) continue;
                return index;
            }
            return fallback;
        }

        private static bool Touches(DantianLayoutProblem problem,
            DantianLayoutPlacement placement, int[] occupancy)
        {
            foreach (var cell in placement.CellIndices)
                if (problem.NeighborIndices[cell].Any(neighbor => occupancy[neighbor] > 0))
                    return true;
            return false;
        }

        private static string DescribeSolution(DantianLayoutProblem problem, int[] placements)
        {
            return string.Join("; ", problem.Pieces.Select((piece, index) =>
            {
                var placement = piece.Placements[placements[index]];
                return $"{piece.Name}=({placement.X},{placement.Y})/r{placement.Rotation}";
            }));
        }

        private static string DescribeRuntimeBonuses()
        {
            var records = BsSaveDataImpl.nowActor?.dantianUpStoList ?? new List<TbDantianUpSto>();
            return string.Join(", ", records
                .Where(record => record != null && record.npcStoId == 10000)
                .GroupBy(record => $"{(int)record.fromUpdate.blendEnum}:{record.fromUpdate.sedId}:{record.drawStateId}")
                .Select(group => $"{group.Key}=x{group.Max(record => record.UpMultiplier)}"));
        }

        private IEnumerator ShowTemporary(string message)
        {
            SetLabel(message);
            yield return new WaitForSecondsRealtime(1.8f);
            SetLabel("优化排布");
            if (_button != null) _button.interactable = true;
            _running = null;
        }

        private void SetLabel(string text)
        {
            if (_label != null) _label.text = text;
        }

        private void OnDisable()
        {
            DantianSyntheticLayoutContext.Current = null;
            if (_running != null) StopCoroutine(_running);
            _running = null;
            if (_button != null) _button.interactable = true;
            SetLabel("优化排布");
        }
    }

    [HarmonyPatch(typeof(DantianPanel), "InitPanel")]
    internal static class DantianOptimizerUiPatch
    {
        private static void Postfix(DantianPanel __instance)
        {
            var ui = __instance.gameObject.GetComponent<DantianOptimizerUi>() ??
                     __instance.gameObject.AddComponent<DantianOptimizerUi>();
            ui.Initialize(__instance);
        }
    }
}
