from collections import Counter
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Tuple


@dataclass
class ListeningEvent:
    timestamp: datetime
    artist_name: str
    track_name: str
    ms_played: int
    spotify_uri: Optional[str] = None


@dataclass
class Era:
    id: int
    start_date: date
    end_date: date
    top_artists: List[Tuple[str, int]]  # (artist_name, play_count)
    top_tracks: List[Tuple[str, str, int]]  # (track_name, artist_name, play_count)
    total_ms_played: int
    title: str = ""
    summary: str = ""


@dataclass
class Playlist:
    era_id: int
    tracks: List[dict]  # {track_name, artist_name, uri}


@dataclass
class WeekBucket:
    week_key: Tuple[int, int]  # (year, week_number) to handle year boundaries
    week_start: date
    artists: Counter  # Counter of artist_name -> play_count
    tracks: Counter  # Counter of (track_name, artist_name) -> play_count
    total_ms: int
