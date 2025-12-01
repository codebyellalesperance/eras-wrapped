from datetime import datetime
from typing import List

import orjson

from models import ListeningEvent


class ParseError(Exception):
    """Raised when parsing fails."""
    pass


def parse_spotify_json(file_content: bytes) -> List[ListeningEvent]:
    """
    Parse a single Spotify extended streaming history JSON file.

    Args:
        file_content: Raw bytes of the JSON file

    Returns:
        List of ListeningEvent objects

    Raises:
        ParseError: If JSON is malformed or data is invalid
    """
    try:
        data = orjson.loads(file_content)
    except orjson.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON: {e}")

    if not isinstance(data, list):
        raise ParseError("Expected JSON array of listening events")

    events = []
    seen = set()  # For deduplication

    for entry in data:
        # Skip entries with missing required fields
        track_name = entry.get('master_metadata_track_name')
        artist_name = entry.get('master_metadata_album_artist_name')
        ms_played = entry.get('ms_played', 0)
        ts = entry.get('ts')

        # Filter out invalid entries
        if track_name is None or artist_name is None:
            continue
        if ms_played < 30000:  # Less than 30 seconds
            continue
        if ts is None:
            continue

        # Parse timestamp (Spotify uses ISO 8601 with Z suffix)
        try:
            timestamp = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            continue  # Skip entries with invalid timestamps

        # Deduplicate by (timestamp, track, artist)
        dedup_key = (ts, track_name, artist_name)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        event = ListeningEvent(
            timestamp=timestamp,
            artist_name=artist_name,
            track_name=track_name,
            ms_played=ms_played,
            spotify_uri=entry.get('spotify_track_uri')
        )
        events.append(event)

    return events
