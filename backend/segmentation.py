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


def calculate_similarity(week_a: WeekBucket, week_b: WeekBucket) -> float:
    """
    Calculate Jaccard similarity between two weeks based on top artists.

    Args:
        week_a: First week bucket
        week_b: Second week bucket

    Returns:
        Float between 0.0 and 1.0 representing similarity
    """
    # Get top N artists from each week
    n = min(20, len(week_a.artists), len(week_b.artists))

    if n == 0:
        return 0.0

    # Extract artist names from top N
    top_a = set(artist for artist, _ in week_a.artists.most_common(n))
    top_b = set(artist for artist, _ in week_b.artists.most_common(n))

    # Calculate Jaccard similarity
    intersection = len(top_a & top_b)
    union = len(top_a | top_b)

    if union == 0:
        return 0.0

    return intersection / union


def detect_era_boundaries(weeks: List[WeekBucket], threshold: float = 0.3) -> List[int]:
    """
    Detect boundaries between eras based on listening pattern changes.

    Args:
        weeks: List of WeekBucket objects sorted by week_start
        threshold: Similarity threshold below which a new era starts (0.0-1.0)
                   Lower = more eras, Higher = fewer eras

    Returns:
        List of week indices where new eras start (always includes 0)
    """
    if not weeks:
        return []

    if len(weeks) == 1:
        return [0]

    boundaries = [0]  # First week is always a boundary

    for i in range(1, len(weeks)):
        # Check for gap in listening (more than 4 weeks)
        gap_days = (weeks[i].week_start - weeks[i - 1].week_start).days
        if gap_days > 28:  # More than 4 weeks gap
            boundaries.append(i)
            continue

        # Check similarity with previous week
        similarity = calculate_similarity(weeks[i - 1], weeks[i])
        if similarity < threshold:
            boundaries.append(i)

    return boundaries
