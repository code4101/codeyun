using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using HarmonyLib;
using Newtonsoft.Json;
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
        internal int Potential;
        internal List<int> Multipliers = new List<int>();
        internal List<string> RuleDetails = new List<string>();
        internal List<string> RuleEvidence = new List<string>();

        // Potential 只负责引导搜索跨越“每 N 个才 +1”的同分平台；精确倍率始终是主目标。
        internal double Fitness => Balance + Total * 0.000001d + Potential * 0.0001d;

        internal bool BetterThan(DantianLayoutScore other)
        {
            if (other == null) return true;
            if (Balance > other.Balance + 0.0000001d) return true;
            if (Math.Abs(Balance - other.Balance) > 0.0000001d) return false;
            if (Total != other.Total) return Total > other.Total;
            return Potential > other.Potential;
        }

        internal bool ExactBetterThan(DantianLayoutScore other)
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
            return $"balanced={geometric:F3}, total={Total}, potential={Potential}, " +
                   $"rules=[{string.Join(",", Multipliers)}]";
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
        internal bool IsArt;
        internal bool IsMagic;
        internal int Attribute = -1;
        internal int Type;
        internal bool IsAttackMagic;
        internal bool IsDefenceMagic;
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
                    for (var ruleIndex = 0; ruleIndex < piece.Rules.Count; ruleIndex++)
                    {
                        var rule = piece.Rules[ruleIndex];
                        var targets = controller.GetVaildArtMagicIdList(source, layout, rule.targetEff);
                        var targetCells = targets ?? new List<TbDantianSto>();
                        var targetObjects = targetCells
                            .Where(cell => cell.artMagicId.sedId != 0)
                            .Select(cell => cell.artMagicId)
                            .Distinct()
                            .ToList();
                        var hasTarget = targetObjects.Count != 0;
                        var multiplier = hasTarget && GetUpMultiplierMethod != null
                            ? (int)GetUpMultiplierMethod.Invoke(controller, new object[]
                            {
                                source, rule.upMulEff, rule.upMulType, rule.maxUpMul,
                            })
                            : 0;
                        multiplier = Math.Max(0, multiplier);
                        var progressTargets = controller.GetVaildArtMagicIdList(
                            source, layout, rule.upMulEff);
                        var upCells = progressTargets ?? new List<TbDantianSto>();
                        var upObjects = upCells
                            .Where(cell => cell.artMagicId.sedId != 0)
                            .Select(cell => cell.artMagicId)
                            .Distinct()
                            .ToList();
                        var potential = upObjects.Count;
                        score.RuleCount++;
                        score.Total += multiplier;
                        score.Potential += potential;
                        score.Balance += Math.Log(1d + multiplier);
                        score.Multipliers.Add(multiplier);
                        score.RuleDetails.Add(
                            $"{piece.Name}#{ruleIndex + 1}=x{multiplier}/progress{potential}");
                        score.RuleEvidence.Add(
                            $"{piece.Name}#{ruleIndex + 1} " +
                            $"targetEff='{rule.targetEff}' targetCells={targetCells.Count} " +
                            $"targetObjects={targetObjects.Count}[{string.Join(",", targetObjects.Select(BlendKey))}] " +
                            $"upEff='{rule.upMulEff}' upType='{rule.upMulType}' max={rule.maxUpMul} " +
                            $"upCells={upCells.Count} upObjects={upObjects.Count}" +
                            $"[{string.Join(",", upObjects.Select(BlendKey))}] " +
                            $"upRows={upCells.Select(cell => cell.y).Distinct().Count()} " +
                            $"upCols={upCells.Select(cell => cell.x).Distinct().Count()} " +
                            $"nativeMultiplier={multiplier}");
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

        internal static bool MatchesCurrentLayout(DantianLayoutProblem problem, int[] placements)
        {
            var controller = Singleton<DantianController>.Instance;
            return controller != null && MatchesCurrentLayout(controller, problem, placements);
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
            var isArt = false;
            var isMagic = false;
            var attribute = -1;
            var type = 0;
            var isAttackMagic = false;
            var isDefenceMagic = false;

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
                isArt = true;
                attribute = cfg.attribute;
                type = cfg.type;
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
                isMagic = true;
                attribute = cfg.attribute;
                type = cfg.type;
                var effectTypes = cfg.GetEffType;
                isAttackMagic = effectTypes != null && effectTypes.Any(value =>
                    string.Equals(value.ToString(), "Attack", StringComparison.Ordinal));
                isDefenceMagic = effectTypes != null && effectTypes.Any(value =>
                    string.Equals(value.ToString(), "Defence", StringComparison.Ordinal));
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
                IsArt = isArt,
                IsMagic = isMagic,
                Attribute = attribute,
                Type = type,
                IsAttackMagic = isAttackMagic,
                IsDefenceMagic = isDefenceMagic,
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
        private const int MaxIterations = 120000;
        private const int MaxMilliseconds = 20000;
        private const int GreedyMilliseconds = 2400;
        private const int KickInterval = 1800;
        private DantianPanel _panel;
        private Button _button;
        private TextPro _label;
        private Coroutine _running;
        private CancellationTokenSource _solveCancellation;
        private int _runId;

        internal void Initialize(DantianPanel panel)
        {
            if (_panel != null) return;
            _panel = panel;
            var view = Traverse.Create(panel).Field<DantianPanelView>("view").Value;
            if (view == null) return;
            var reset = view.DantianOperationArea.btnResetDantian;
            var combination = view.DantianOperationArea.togSelectCombination;
            if (reset == null || combination == null) return;

            // “清空 / 重塑 / 扩排”是原生专属动作组，不能再向其 LayoutGroup 插入四字按钮。
            // 优化作用于当前组合方案，因此以方案选择框为锚点，横向放在它的正上方。
            var controlParent = combination.transform.parent;
            _button = Instantiate(reset, controlParent);
            _button.gameObject.name = "Code4101DantianOptimize";
            _button.onClick.RemoveAllListeners();
            foreach (var localization in _button.GetComponentsInChildren<TextProLocalization>(true))
                localization.enabled = false;
            var labels = _button.GetComponentsInChildren<TextPro>(true);
            _label = labels.FirstOrDefault();
            if (_label != null)
            {
                _label.text = "优化排布";
                _label.alignment = TMPro.TextAlignmentOptions.Center;
                _label.enableWordWrapping = false;
                _label.enableAutoSizing = true;
                _label.fontSizeMin = 18f;
                _label.fontSizeMax = 30f;
                var labelRect = (RectTransform)_label.transform;
                labelRect.anchorMin = Vector2.zero;
                labelRect.anchorMax = Vector2.one;
                labelRect.pivot = new Vector2(0.5f, 0.5f);
                labelRect.offsetMin = new Vector2(8f, 3f);
                labelRect.offsetMax = new Vector2(-8f, -3f);
            }
            for (var i = 1; i < labels.Length; i++) labels[i].text = string.Empty;

            var rect = (RectTransform)_button.transform;
            rect.anchorMin = new Vector2(0.5f, 0.5f);
            rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.sizeDelta = new Vector2(180f, 50f);
            var combinationBounds = RectTransformUtility.CalculateRelativeRectTransformBounds(
                controlParent, combination.transform);
            rect.localPosition = new Vector3(
                combinationBounds.center.x,
                combinationBounds.max.y + rect.sizeDelta.y * 0.5f + 8f,
                combination.transform.localPosition.z);
            var layoutElement = _button.GetComponent<LayoutElement>() ??
                                _button.gameObject.AddComponent<LayoutElement>();
            layoutElement.ignoreLayout = true;
            _button.transform.SetAsLastSibling();
            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:UI] " +
                                  $"parent={controlParent.name} position={rect.localPosition} " +
                                  $"size={rect.sizeDelta} anchor=combination");
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
            var seed = unchecked(BsSaveDataImpl.nowActor.randomSeed + runId * 7919);
            _solveCancellation?.Cancel();
            _solveCancellation?.Dispose();
            _solveCancellation = new CancellationTokenSource();
            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] begin-model " +
                                  $"board={problem.Board.Count} pieces={problem.Pieces.Count} " +
                                  $"rules={rules} placements={candidates} " +
                                  $"ruleDefinitions=[{DescribeRules(problem)}]");
            foreach (var evidence in currentScore.RuleEvidence)
                UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                      $"native-oracle {evidence}");
            var buildProgress = new DantianSolverBuildProgress
            {
                TotalOptions = rules,
            };
            var buildTask = DantianCpSatBridge.BuildAsync(problem, currentScore,
                MaxMilliseconds, seed, buildProgress, _solveCancellation.Token);
            var displayedProgress = -1;
            while (!buildTask.IsCompleted)
            {
                yield return null;
                var completed = Volatile.Read(ref buildProgress.CompletedOptions);
                if (completed != displayedProgress)
                {
                    displayedProgress = completed;
                    SetLabel($"校验规则 {completed}/{buildProgress.TotalOptions}…");
                }
                if (_panel != null && _panel.gameObject.activeInHierarchy) continue;
                _solveCancellation.Cancel();
                yield break;
            }
            if (buildTask.IsCanceled)
            {
                yield return ShowTemporary("求解已取消");
                yield break;
            }
            if (buildTask.IsFaulted)
            {
                var buildException = buildTask.Exception?.GetBaseException();
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                           $"model-failed {buildException?.GetType().Name}: " +
                                           $"{buildException?.Message}");
                yield return ShowTemporary("规则建模失败");
                yield break;
            }
            var buildResult = buildTask.Result;
            if (buildResult.Request == null)
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                           $"model-failed {buildResult.Error}");
                yield return ShowTemporary("规则建模失败");
                yield break;
            }
            var request = buildResult.Request;
            var jsonBytes = JsonConvert.SerializeObject(request).Length;
            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] begin-cpsat " +
                                  $"board={problem.Board.Count} pieces={problem.Pieces.Count} rules={rules} " +
                                  $"placements={candidates} modelMs={buildResult.ElapsedMilliseconds} " +
                                  $"jsonChars={jsonBytes} current={currentScore} " +
                                  $"currentRules=[{string.Join(", ", currentScore.RuleDetails)}] " +
                                  $"ruleDefinitions=[{DescribeRules(problem)}]");

            SetLabel("求解中…");
            var solveTask = DantianCpSatBridge.RunAsync(request, _solveCancellation.Token);
            while (!solveTask.IsCompleted)
            {
                yield return null;
                if (_panel != null && _panel.gameObject.activeInHierarchy) continue;
                _solveCancellation.Cancel();
                yield break;
            }
            var runResult = solveTask.Result;
            if (!string.IsNullOrEmpty(runResult.SnapshotPath))
                UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                      $"snapshot={runResult.SnapshotPath}");
            if (!string.IsNullOrEmpty(runResult.Error))
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                           $"solver-failed {runResult.Error} stderr={runResult.StandardError}");
                yield return ShowTemporary("求解器失败");
                yield break;
            }
            var response = runResult.Response;
            if (response == null || (response.status != "OPTIMAL" && response.status != "FEASIBLE"))
            {
                UnityEngine.Debug.LogWarning($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                             $"solver-status={response?.status ?? "null"} " +
                                             $"workerModel={response?.modelBuildSeconds:F3}s " +
                                             $"solve={response?.elapsedSeconds:F3}s " +
                                             $"total={response?.totalSeconds:F3}s");
                yield return ShowTemporary(response?.status == "UNKNOWN"
                    ? "求解超时，保留原布局"
                    : "模型未返回可行解");
                yield break;
            }
            var best = response.placements;
            if (!DantianCpSatBridge.ValidateSolution(problem, best, out var validationError))
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                           $"invalid-solution {validationError}");
                yield return ShowTemporary("求解结果无效");
                yield break;
            }
            if (!DantianLayoutOptimizer.TryEvaluate(problem, best, out var bestScore, out scoreError))
            {
                UnityEngine.Debug.LogError($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                           $"native-verify-failed {scoreError}");
                yield return ShowTemporary("原生复核失败");
                yield break;
            }
            UnityEngine.Debug.Log($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] solved-cpsat " +
                                  $"status={response.status} workerModel={response.modelBuildSeconds:F3}s " +
                                  $"solve={response.elapsedSeconds:F3}s total={response.totalSeconds:F3}s " +
                                  $"objective={response.objective:F0} bound={response.bestBound:F0} " +
                                  $"solverProduct={response.product} solverTotal={response.total} " +
                                  $"nativeBest={bestScore} nativeRules=[{string.Join(", ", bestScore.RuleDetails)}] " +
                                  $"solution=[{DescribeSolution(problem, best)}]");
            if (!bestScore.ExactBetterThan(currentScore))
            {
                yield return ShowTemporary("暂未找到更优");
                yield break;
            }

            // Modeling and solving are intentionally asynchronous.  Do not overwrite a layout
            // the player changed while the worker was running; only the short native apply step
            // is allowed to be atomic on the Unity main thread.
            if (!DantianLayoutOptimizer.MatchesCurrentLayout(problem, current))
            {
                UnityEngine.Debug.LogWarning($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                             "stale-solution current-layout-changed");
                yield return ShowTemporary("布局已变化，请重试");
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
                                  $"before={currentScore} predicted={bestScore} actual={actual} " +
                                  $"actualRules=[{string.Join(", ", actual?.RuleDetails ?? new List<string>())}]");
            try
            {
                _panel?.InitPanel();
            }
            catch (Exception refreshException)
            {
                UnityEngine.Debug.LogWarning($"[Code4101 Tiandao][DantianOptimizer:{runId:D4}] " +
                                             $"panel-refresh-failed {refreshException.Message}");
            }
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
            for (var attempt = 0; attempt < 72; attempt++)
            {
                var index = random.Next(piece.Placements.Count);
                var placement = piece.Placements[index];
                if (placement.CellIndices.Any(cell => occupancy[cell] > 0)) continue;
                return index;
            }

            // 随机试探没撞到空位时，从随机起点完整扫描，避免候选很多时误判无处可放。
            var start = random.Next(piece.Placements.Count);
            for (var offset = 0; offset < piece.Placements.Count; offset++)
            {
                var index = (start + offset) % piece.Placements.Count;
                if (!piece.Placements[index].CellIndices.Any(cell => occupancy[cell] > 0))
                    return index;
            }
            return -1;
        }

        private static int ApplyKick(DantianLayoutProblem problem, int[] working,
            int[] occupancy, System.Random random, int moveCount)
        {
            var moved = 0;
            foreach (var pieceIndex in Enumerable.Range(0, problem.Pieces.Count)
                         .OrderBy(_ => random.Next()).Take(moveCount))
            {
                var piece = problem.Pieces[pieceIndex];
                var oldPlacementIndex = working[pieceIndex];
                foreach (var cell in piece.Placements[oldPlacementIndex].CellIndices)
                    occupancy[cell]--;
                var newPlacementIndex = PickPlacement(problem, piece, occupancy, random);
                if (newPlacementIndex < 0)
                    newPlacementIndex = oldPlacementIndex;
                else if (newPlacementIndex != oldPlacementIndex)
                    moved++;
                working[pieceIndex] = newPlacementIndex;
                foreach (var cell in piece.Placements[newPlacementIndex].CellIndices)
                    occupancy[cell]++;
            }
            return moved;
        }

        private static string DescribeRules(DantianLayoutProblem problem)
        {
            return string.Join("; ", problem.Pieces.SelectMany(piece =>
                piece.Rules.Select((rule, index) =>
                    $"{piece.Name}#{index + 1}:target={rule.targetEff}," +
                    $"up={rule.upMulEff},type={rule.upMulType},max={rule.maxUpMul}")));
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
            _solveCancellation?.Cancel();
            _solveCancellation?.Dispose();
            _solveCancellation = null;
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
