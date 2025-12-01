from collections import Counter
from datetime import date, timedelta
from typing import List

from models import ListeningEvent, WeekBucket


def aggregate_by_week(events: List[ListeningEvent]) -> List[WeekBucket]:
    """
    Group listening events by ISO week.

    Args:
        events: List of ListeningEvent objects

    Returns:
        List of WeekBucket objects sorted by week_start
    """
    if not events:
        return []

    # Group events by (year, week)
    weeks_data = {}

    for event in events:
        iso_cal = event.timestamp.isocalendar()
        week_key = (iso_cal[0], iso_cal[1])  # (year, week)

        if week_key not in weeks_data:
            # Calculate Monday of this ISO week
            # ISO week 1 contains Jan 4, and weeks start on Monday
            jan4 = date(iso_cal[0], 1, 4)
            week_start = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=iso_cal[1] - 1)

            weeks_data[week_key] = {
                'week_start': week_start,
                'artists': Counter(),
                'tracks': Counter(),
                'total_ms': 0
            }

        weeks_data[week_key]['artists'][event.artist_name] += 1
        weeks_data[week_key]['tracks'][(event.track_name, event.artist_name)] += 1
        weeks_data[week_key]['total_ms'] += event.ms_played

    # Convert to WeekBucket objects
    buckets = [
        WeekBucket(
            week_key=week_key,
            week_start=data['week_start'],
            artists=data['artists'],
            tracks=data['tracks'],
            total_ms=data['total_ms']
        )
        for week_key, data in weeks_data.items()
    ]

    # Sort by week_start
    buckets.sort(key=lambda b: b.week_start)

    return buckets
