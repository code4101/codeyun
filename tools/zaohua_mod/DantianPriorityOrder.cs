using System.Collections.Generic;
using System.Linq;

namespace Code4101.Dantian.Common
{
    internal static class DantianPriorityOrder
    {
        internal static List<string> ForAvailable(IEnumerable<string> savedOrder,
            IEnumerable<string> availableKeys)
        {
            var available = availableKeys.Where(key => !string.IsNullOrEmpty(key))
                .Distinct().ToList();
            var availableSet = new HashSet<string>(available);
            var result = savedOrder.Where(availableSet.Contains).Distinct().ToList();
            result.AddRange(available.Where(key => !result.Contains(key)));
            return result;
        }

        internal static List<string> ForSave(IEnumerable<string> savedOrder,
            IEnumerable<string> visibleOrder)
        {
            var visible = visibleOrder.Where(key => !string.IsNullOrEmpty(key))
                .Distinct().ToList();
            var visibleSet = new HashSet<string>(visible);
            visible.AddRange(savedOrder.Where(key => !string.IsNullOrEmpty(key) &&
                                                   !visibleSet.Contains(key)).Distinct());
            return visible;
        }
    }
}
