"""Owner seq3023: choose the latest day without losing estimated players."""
from datetime import date, timedelta


def plan_xutian_yandi_day(*, start_date: date, first_day_population: int,
                         map_definitions: list[dict], thresholds: list[dict]) -> dict:
    """Use the day-one 21:00 census and current native promotion caps.

    Day one has no admission cap. Each following day's cap is the previous
    map's threshold. Later days win ties: 50 active players with a final cap
    of 100 can wait until the final day. No future population is claimed.
    """
    if type(first_day_population) is not int or first_day_population <= 0:
        raise ValueError("虚天21点基准人数必须是正整数")
    maps = sorted(map_definitions, key=lambda row: int(row['sort']))
    if [int(row['sort']) for row in maps] != [1, 2, 3, 4]:
        raise ValueError("虚天四层地图顺序不完整")
    caps = {int(row['heavenId']): int(row['threshold']) for row in thresholds}
    if len(caps) != 4 or any(int(m['id']) not in caps for m in maps):
        raise ValueError("虚天晋级门槛与地图身份不一致")
    days = []
    surviving_cap = first_day_population
    for index, row in enumerate(maps):
        cap = None if index == 0 else caps[int(maps[index - 1]['id'])]
        if cap is not None and cap <= 0:
            raise ValueError("虚天中间地图的晋级门槛必须为正")
        if cap is not None:
            surviving_cap = min(surviving_cap, cap)
        days.append({'date': (start_date + timedelta(days=index)).isoformat(),
                     'day_index': index + 1, 'heaven_id': int(row['id']),
                     'admission_cap': cap,
                     'estimated_players': surviving_cap})
    selected = max(days, key=lambda row: (row['estimated_players'], row['day_index']))
    return {'selected_date': selected['date'], 'expected_heaven': selected['heaven_id'],
            'first_day_population': first_day_population, 'days': days,
            'policy': 'owner3023_latest_day_retaining_max_estimated_players'}
