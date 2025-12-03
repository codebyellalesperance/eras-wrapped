# Spotify Eras — Step-by-Step Build Guide

## PHASE 0: Project Setup

### Step 0.1 — Initialize Project Structure
Create the following folder structure:
```
spotify-eras/
├── backend/
│   ├── __init__.py
│   ├── app.py
│   ├── requirements.txt
│   ├── parser.py
│   ├── segmentation.py
│   ├── llm_service.py
│   ├── playlist_builder.py
│   └── models.py
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── .env.example
└── README.md
```

### Step 0.2 — Define Python Dependencies
Create `requirements.txt` with:
- flask
- flask-cors
- python-dotenv
- orjson (faster JSON parsing for large Spotify exports)
- gunicorn (production WSGI server)
- openai (or anthropic, depending on LLM choice)

### Step 0.2.1 — Create Environment File Template
Create `.env.example` with:
```
OPENAI_API_KEY=your_api_key_here
# Or if using Anthropic:
# ANTHROPIC_API_KEY=your_api_key_here
FLASK_ENV=development
```

### Step 0.3 — Define Data Models
In `models.py`, create these dataclasses:

```python
from dataclasses import dataclass, field
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
```

---

## PHASE 1: Backend — File Parsing

### Step 1.1 — Create Basic Flask App
In `app.py`:
- Load environment variables: `from dotenv import load_dotenv; load_dotenv()`
- Initialize Flask app
- Set max upload size: `app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB`
- Enable CORS (restrict origins in production): `CORS(app, origins=os.getenv('ALLOWED_ORIGINS', '*').split(','))`
- Create in-memory session store: `sessions = {}`
- Add session cleanup: store `created_at` timestamp with each session, periodically remove sessions older than 1 hour
- Create route `GET /health` that returns `{"status": "ok"}`

### Step 1.2 — Create Upload Endpoint
Create `POST /upload` endpoint that:
- Accepts multipart form data with file(s)
- Validate file exists in request, return `{"error": "No file provided"}` with 400 status if missing
- Validate file type (check both extension AND magic bytes for ZIP files)
- Only after validation: generate unique session_id (use uuid4)
- Store session object: `sessions[session_id] = {"events": [], "eras": [], "playlists": [], "progress": {"stage": "uploading", "percent": 0}, "created_at": datetime.now()}`
- Returns `{"session_id": session_id}`

### Step 1.3 — Parse Single JSON File
In `parser.py`, create function `parse_spotify_json(file_content: bytes) -> List[ListeningEvent]`:
- Wrap parsing in try/except for `orjson.JSONDecodeError`
- Parse JSON content using `orjson.loads()` for better performance
- Spotify's extended history format has these fields:
  - `ts` (ISO 8601 timestamp string, e.g., `"2023-01-15T14:30:00Z"`)
  - `master_metadata_track_name`
  - `master_metadata_album_artist_name`
  - `ms_played`
  - `spotify_track_uri`
- Filter out entries where `ms_played < 30000` (less than 30 seconds = skip)
- Filter out entries where `master_metadata_track_name` is null
- Filter out entries where `master_metadata_album_artist_name` is null
- Parse timestamp: use `datetime.fromisoformat(ts.replace('Z', '+00:00'))` to handle UTC timezone
- Convert each valid entry to a `ListeningEvent`
- Deduplicate by (timestamp, track_name, artist_name) to handle duplicate entries in exports
- Return list of events

### Step 1.4 — Handle ZIP Upload
Create function `parse_spotify_zip(zip_bytes: bytes) -> List[ListeningEvent]`:
- Use in-memory extraction with `io.BytesIO(zip_bytes)` — do NOT extract to disk
- Validate ZIP file: `zipfile.is_zipfile(bytes_io)`
- Security: validate each filename doesn't contain path traversal (`..` or absolute paths)
- Security: limit total extracted size to prevent zip bombs (e.g., 1GB max)
- Use `fnmatch.fnmatch(name, '*Streaming_History_Audio_*.json')` to find matching files
- Handle nested directories: Spotify sometimes puts files in a subfolder like `my_spotify_data/`
- Call `parse_spotify_json` on each matching file's bytes
- Combine all events into single list
- Sort by timestamp ascending
- Return combined list

### Step 1.5 — Integrate Parsing into Upload
Update `POST /upload` to:
- Detect file type by checking magic bytes: ZIP files start with `PK\x03\x04`
- Also check extension as fallback (`.zip` or `.json`)
- Call appropriate parser, wrapped in try/except
- On parse error: return `{"error": "Failed to parse file: <message>"}` with 400 status
- On success: store parsed events in `sessions[session_id]["events"]`
- Update progress to `{"stage": "parsed", "percent": 20}`
- Process synchronously for MVP (blocking call) — async can be added later with threading or Celery

### Step 1.6 — Create Progress Endpoint
Create `GET /progress/<session_id>` as Server-Sent Events (SSE):
- Return 404 JSON error if session_id not found
- Use `stream_with_context` from Flask for proper generator handling
- Set required headers:
  - `Content-Type: text/event-stream`
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
- Yield current progress state from `sessions[session_id]["progress"]`
- Format: `data: {"stage": "...", "percent": ...}\n\n`
- Send keepalive comment every 15 seconds: `: keepalive\n\n`
- Poll internal state every 500ms
- Continue until stage is "complete" or "error"
- Set a timeout (e.g., 5 minutes max) to prevent hung connections

---

## PHASE 2: Backend — Era Segmentation

### Step 2.0 — Add WeekBucket to Models
In `models.py`, add the `WeekBucket` dataclass:

```python
from collections import Counter

@dataclass
class WeekBucket:
    week_key: Tuple[int, int]  # (year, week_number) to handle year boundaries
    week_start: date
    artists: Counter  # Counter of artist_name -> play_count
    tracks: Counter   # Counter of (track_name, artist_name) -> play_count
    total_ms: int
```

### Step 2.1 — Create Weekly Aggregates
In `segmentation.py`, create function `aggregate_by_week(events: List[ListeningEvent]) -> List[WeekBucket]`:
- Return empty list if events is empty
- Group events by ISO week using `event.timestamp.isocalendar()` — returns `(year, week, weekday)`
- Use `(year, week)` tuple as week key to handle year boundary edge cases
- Calculate `week_start` as the Monday of that ISO week
- For each week:
  - Count artist plays: `Counter[artist_name] -> int`
  - Count track plays: `Counter[(track_name, artist_name)] -> int` (tuple to preserve artist association)
  - Sum `ms_played` for total_ms
- Return list of WeekBuckets sorted by week_start

### Step 2.2 — Calculate Artist Similarity Between Weeks
Create function `calculate_similarity(week_a: WeekBucket, week_b: WeekBucket) -> float`:
- Get top N artists from each week (N = min(20, number of artists in smaller week))
- Extract just the artist names as sets
- Handle edge case: if union is empty, return 0.0 to avoid division by zero
- Calculate Jaccard similarity: `len(A & B) / len(A | B)`
- Return float between 0.0 and 1.0

### Step 2.3 — Detect Era Boundaries
Create function `detect_era_boundaries(weeks: List[WeekBucket], threshold: float = 0.3) -> List[int]`:
- If weeks is empty, return empty list
- If only 1 week, return `[0]`
- Always include index 0 as first boundary
- Compare each consecutive pair of weeks:
  - Calculate gap: `(weeks[i].week_start - weeks[i-1].week_start).days`
  - If gap > 28 days (4 weeks), mark index i as boundary (listening gap)
  - Else if `calculate_similarity(weeks[i-1], weeks[i]) < threshold`, mark as boundary
- Threshold 0.3 is tunable — lower = more eras, higher = fewer eras
- Return list of week indices where new eras start

### Step 2.4 — Build Era Objects
Create function `build_eras(weeks: List[WeekBucket], boundaries: List[int]) -> List[Era]`:
- If weeks is empty, return empty list
- For each era (from boundary[i] to boundary[i+1], or to end for last era):
  - Combine all weeks' artist Counters using `sum(counters, Counter())`
  - Combine all weeks' track Counters similarly
  - Sum total_ms_played from all weeks
  - Get top 10 artists as `List[Tuple[str, int]]` using `.most_common(10)`
  - Get top 20 tracks as `List[Tuple[str, str, int]]` — unpack the (track, artist) key and add count
  - Set start_date = first week's `week_start`
  - Set end_date = last week's `week_start + timedelta(days=6)` (end of that week)
  - Leave title and summary as empty strings (filled by LLM later)
- Return list of Era objects with sequential IDs starting at 1

### Step 2.5 — Filter Insignificant Eras
Create function `filter_eras(eras: List[Era], min_weeks: int = 2, min_ms: int = 3600000) -> List[Era]`:
- Calculate weeks in era: `((era.end_date - era.start_date).days // 7) + 1`
- Remove eras shorter than min_weeks
- Remove eras with less than min_ms (1 hour = 3600000ms default)
- If all eras filtered out, return empty list (don't error)
- Re-number remaining era IDs sequentially starting at 1
- Return filtered list

### Step 2.6 — Integrate Segmentation into Pipeline
Create function `segment_listening_history(events: List[ListeningEvent]) -> List[Era]`:
- Call aggregate_by_week
- Call detect_era_boundaries
- Call build_eras
- Call filter_eras
- Return final era list (may be empty)

In `app.py`, create a new endpoint `POST /process/<session_id>`:
- Validate session exists, return 404 if not
- Validate session has events, return 400 if not
- Wrap processing in try/except
- Call segment_listening_history with session events
- On error: set progress to `{"stage": "error", "message": str(e), "percent": 0}`
- On success with no eras: set progress to `{"stage": "error", "message": "No distinct eras found", "percent": 0}`
- On success: store eras in session, update progress to `{"stage": "segmented", "percent": 40}`
- Optionally: delete `sessions[session_id]["events"]` after segmentation to free memory
- Return `{"status": "ok"}` or error JSON

---

## PHASE 3: Backend — LLM Naming & Summarization

### Step 3.0 — Update Environment Configuration
Add to `.env.example`:
```
# LLM Configuration
LLM_PROVIDER=openai  # or anthropic
LLM_MODEL=gpt-4o-mini  # or claude-3-haiku-20240307
LLM_TIMEOUT=30  # seconds per request
```

### Step 3.1 — Create LLM Service Setup
In `llm_service.py`:
- Import required libraries: `os`, `time`, `re`, `json`
- Load configuration from environment:
  - `LLM_PROVIDER` (default: "openai")
  - `LLM_MODEL` (default: "gpt-4o-mini" for OpenAI, "claude-3-haiku-20240307" for Anthropic)
  - `LLM_TIMEOUT` (default: 30 seconds)
- Create `get_client()` function that initializes the appropriate client based on provider
- Raise clear error if API key is missing: `raise ValueError("OPENAI_API_KEY not set")`
- Create retry decorator with exponential backoff:
  - Max 3 retries
  - Delays: 1s, 2s, 4s
  - Retry on rate limit errors and transient failures

### Step 3.2 — Create Era Naming Prompt
Create function `build_era_prompt(era: Era) -> str`:
- Format date range as human-readable: "March 2021 - August 2021"
- Calculate and format duration: `(end_date - start_date).days` → "5 months" or "12 weeks"
- Format listening time: `total_ms_played // 3600000` → "47 hours"
- Format top 5 artists with play counts: "1. Taylor Swift (156 plays)"
- Format top 10 tracks: "1. Anti-Hero by Taylor Swift (45 plays)"
- Prompt template:
```
You are analyzing someone's music listening history. Based on this era's data, create a creative title and summary.

Era: {formatted_date_range} ({duration})
Total listening time: {hours} hours

Top Artists:
{formatted_artists}

Top Tracks:
{formatted_tracks}

Create a JSON response with:
- "title": A creative, evocative 2-5 word title that captures the mood/vibe. Avoid generic titles like "Musical Journey", "Eclectic Mix", or "Summer Vibes".
- "summary": A 2-3 sentence summary describing the musical mood, themes, or story of this era.

Respond ONLY with valid JSON: {"title": "...", "summary": "..."}
```

### Step 3.3 — Call LLM for Single Era
Create function `name_era(era: Era) -> dict`:
- Build prompt using `build_era_prompt(era)`
- Call LLM API with:
  - `temperature=0.7`
  - `max_tokens=300`
  - Timeout from `LLM_TIMEOUT` env var
- Parse JSON response with fallback:
  - Try `json.loads(response)` first
  - If fails, try regex: `re.search(r'\{.*\}', response, re.DOTALL)` to extract JSON
  - If still fails, return fallback
- Fallback title format: `"Era {era.id}: {month} {year}"` (e.g., "Era 1: March 2021")
- Fallback summary: `"A {duration} period featuring {top_artist} and more."`
- Return `{"title": str, "summary": str}`

### Step 3.4 — Validate LLM Response
Create function `validate_era_name(response: dict, era: Era) -> dict`:
- Check "title" key exists and is non-empty string
- Check "summary" key exists and is non-empty string
- Clean title:
  - Strip leading/trailing whitespace and quotes
  - Remove newlines
  - Truncate to 50 chars if longer
  - If empty after cleaning, use fallback
- Clean summary:
  - Strip leading/trailing whitespace and quotes
  - Collapse multiple spaces/newlines to single space
  - Truncate to 500 chars if longer
  - If < 20 chars after cleaning, use fallback
- Return cleaned `{"title": str, "summary": str}` or fallback values

### Step 3.5 — Process All Eras
Create function `name_all_eras(eras: List[Era], progress_callback: Callable[[int], None]) -> List[Era]`:
- `progress_callback` signature: takes single int (percent 0-100), returns None
- For each era (index i):
  - Try to call `name_era(era)`
  - Validate with `validate_era_name(response, era)`
  - Update `era.title` and `era.summary`
  - Calculate progress: `40 + int((i + 1) / len(eras) * 30)` (40% to 70%)
  - Call `progress_callback(progress_percent)`
  - On exception: log error, use fallback, continue to next era
- Return updated eras list (all eras will have titles, either from LLM or fallback)

### Step 3.6 — Integrate LLM into Pipeline
Extend the `/process/<session_id>` endpoint in `app.py`:
- After segmentation succeeds (eras stored, progress at 40%):
- Create progress callback that updates `session["progress"]["percent"]`
- Call `name_all_eras(eras, progress_callback)`
- Update session with named eras
- Update progress to `{"stage": "named", "percent": 70}`
- Continue to next phase (playlist generation)

---

## PHASE 4: Backend — Playlist Generation

Note: URIs are not available in Era.top_tracks (lost during aggregation). Playlists will contain track/artist names only. Future Spotify API integration would need to search for tracks by name.

### Step 4.1 — Create Playlist Builder Module
Create `playlist_builder.py` with imports:
```python
from typing import List
from models import Era, Playlist
```

### Step 4.2 — Build Playlist Function
Create function `build_playlist(era: Era) -> Playlist`:
- Extract tracks from era.top_tracks (already limited to 20 in segmentation)
- Format each track as dict: `{"track_name": name, "artist_name": artist, "play_count": count}`
- Note: URI is None since it's not preserved through aggregation
- Create and return Playlist object with era_id and track list

```python
def build_playlist(era: Era) -> Playlist:
    tracks = [
        {
            "track_name": track_name,
            "artist_name": artist_name,
            "play_count": count,
            "uri": None  # Not available after aggregation
        }
        for track_name, artist_name, count in era.top_tracks
    ]
    return Playlist(era_id=era.id, tracks=tracks)
```

### Step 4.3 — Build All Playlists
Create function `build_all_playlists(eras: List[Era]) -> List[Playlist]`:
- Iterate through eras and call build_playlist for each
- Return list of Playlist objects

```python
def build_all_playlists(eras: List[Era]) -> List[Playlist]:
    return [build_playlist(era) for era in eras]
```

### Step 4.4 — Integrate Playlists into Pipeline
Update `/process/<session_id>` in `app.py`:
- Import `build_all_playlists` from `playlist_builder`
- After LLM naming completes (progress at 70%):
- Update progress to `{"stage": "playlists", "percent": 80}`
- Call `build_all_playlists(eras)`
- Store playlists in session: `session["playlists"] = playlists`
- Update progress to `{"stage": "complete", "percent": 100}`
- Wrap in try/except - on failure, still mark complete but with empty playlists

---

## PHASE 5: Backend — API Endpoints

### Step 5.0 — Store Aggregate Stats Before Deleting Events
Before deleting events in Step 2.6, calculate and store aggregate statistics that will be needed by the API:

In `segmentation.py`, create function `calculate_aggregate_stats(events: List[ListeningEvent]) -> dict`:
```python
def calculate_aggregate_stats(events: List[ListeningEvent]) -> dict:
    unique_tracks = set((e.track_name, e.artist_name) for e in events)
    unique_artists = set(e.artist_name for e in events)
    return {
        "total_tracks": len(unique_tracks),
        "total_artists": len(unique_artists),
        "total_ms": sum(e.ms_played for e in events),
        "date_range": {
            "start": min(e.timestamp for e in events).date().isoformat(),
            "end": max(e.timestamp for e in events).date().isoformat()
        }
    }
```

Update `/process/<session_id>` to call this before segmentation and store in `session["stats"]`.

### Step 5.1 — Create Session Validation Helper
Create helper function to validate session state before returning data:

```python
def validate_session_ready(session_id: str) -> Tuple[dict, Optional[Tuple[dict, int]]]:
    """Returns (session, None) if valid, or (None, error_response) if invalid."""
    if session_id not in sessions:
        return None, ({"error": "Session not found"}, 404)

    session = sessions[session_id]

    # Update last accessed time for TTL
    session["last_accessed"] = datetime.now()

    if session["progress"]["stage"] == "error":
        return None, ({"error": session["progress"].get("message", "Processing failed")}, 400)

    if session["progress"]["stage"] != "complete":
        return None, ({"error": "Processing not complete", "stage": session["progress"]["stage"]}, 425)

    return session, None
```

### Step 5.2 — Create Serialization Helpers
Create functions to convert dataclasses to JSON-serializable dicts:

```python
def serialize_era_summary(era: Era) -> dict:
    """Serialize era for list view (minimal data)."""
    return {
        "id": era.id,
        "title": era.title,
        "start_date": era.start_date.isoformat(),
        "end_date": era.end_date.isoformat(),
        "top_artists": [{"name": name, "plays": count} for name, count in era.top_artists[:3]],
        "playlist_track_count": len(era.top_tracks)
    }

def serialize_era_detail(era: Era, playlist: Optional[Playlist]) -> dict:
    """Serialize era for detail view (full data)."""
    return {
        "id": era.id,
        "title": era.title,
        "summary": era.summary,
        "start_date": era.start_date.isoformat(),
        "end_date": era.end_date.isoformat(),
        "total_ms_played": era.total_ms_played,
        "top_artists": [{"name": name, "plays": count} for name, count in era.top_artists],
        "top_tracks": [{"track": track, "artist": artist, "plays": count} for track, artist, count in era.top_tracks],
        "playlist": {
            "era_id": playlist.era_id,
            "tracks": playlist.tracks
        } if playlist else None
    }
```

### Step 5.3 — Create Summary Endpoint
Create `GET /session/<session_id>/summary`:
- Call `validate_session_ready()`, return error if invalid
- Return JSON with data from `session["stats"]` and eras:

```python
@app.route('/session/<session_id>/summary')
def get_summary(session_id):
    session, error = validate_session_ready(session_id)
    if error:
        return jsonify(error[0]), error[1]

    stats = session["stats"]
    eras = session["eras"]

    return jsonify({
        "total_eras": len(eras),
        "date_range": stats["date_range"],
        "total_listening_time_ms": stats["total_ms"],
        "total_tracks": stats["total_tracks"],
        "total_artists": stats["total_artists"]
    })
```

### Step 5.4 — Create Eras List Endpoint
Create `GET /session/<session_id>/eras`:
- Call `validate_session_ready()`, return error if invalid
- Return JSON array of era summaries sorted by start_date:

```python
@app.route('/session/<session_id>/eras')
def get_eras(session_id):
    session, error = validate_session_ready(session_id)
    if error:
        return jsonify(error[0]), error[1]

    eras = sorted(session["eras"], key=lambda e: e.start_date)
    return jsonify([serialize_era_summary(era) for era in eras])
```

### Step 5.5 — Create Era Detail Endpoint
Create `GET /session/<session_id>/eras/<era_id>`:
- Call `validate_session_ready()`, return error if invalid
- Validate `era_id` is an integer, return 400 if not
- Find era by ID, return 404 if not found
- Find associated playlist by `era_id`
- Return full serialized era with playlist:

```python
@app.route('/session/<session_id>/eras/<era_id>')
def get_era_detail(session_id, era_id):
    session, error = validate_session_ready(session_id)
    if error:
        return jsonify(error[0]), error[1]

    # Validate era_id format
    try:
        era_id = int(era_id)
    except ValueError:
        return jsonify({"error": "Invalid era_id format"}), 400

    # Find era
    era = next((e for e in session["eras"] if e.id == era_id), None)
    if not era:
        return jsonify({"error": "Era not found"}), 404

    # Find associated playlist
    playlist = next((p for p in session["playlists"] if p.era_id == era_id), None)

    return jsonify(serialize_era_detail(era, playlist))
```

### Step 5.6 — Update Session Cleanup for Activity-Based TTL
Update the session cleanup logic from Step 1.1 to use last access time instead of creation time:

```python
# In session creation (Step 1.2), store both timestamps:
sessions[session_id] = {
    "events": [],
    "eras": [],
    "playlists": [],
    "stats": {},
    "progress": {"stage": "uploading", "percent": 0},
    "created_at": datetime.now(),
    "last_accessed": datetime.now()
}

# In cleanup, check last_accessed instead of created_at:
def cleanup_expired_sessions():
    now = datetime.now()
    expired = [
        sid for sid, session in sessions.items()
        if (now - session["last_accessed"]).total_seconds() > 3600  # 1 hour idle
    ]
    for sid in expired:
        del sessions[sid]
```

### Step 5.7 — Add Rate Limiting (Production)
Install `flask-limiter` and add rate limiting to prevent abuse:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per minute"]
)

# Apply stricter limits to expensive endpoints
@app.route('/process/<session_id>', methods=['POST'])
@limiter.limit("5 per minute")
def process_session(session_id):
    ...
```

Add `flask-limiter` to `requirements.txt`.

### Step 5.8 — Error Response Consistency
All error responses must follow this format:
```json
{
    "error": "Human-readable error message"
}
```

HTTP status codes:
- `400` — Bad request (invalid input, invalid era_id format)
- `404` — Not found (session or era doesn't exist)
- `425` — Too Early (processing not complete)
- `429` — Too Many Requests (rate limited)
- `500` — Internal Server Error (unexpected failures)

---

## PHASE 6: Frontend — Landing Page

### Step 6.0 — Application State Management
At the top of `app.js`, define application state, configuration, and utility functions. This shows the overall structure - individual pieces will be detailed in subsequent steps:

```javascript
// Configuration
const API_URL = window.location.hostname === 'localhost'
    ? 'http://localhost:5000'
    : '';  // Same origin in production
const MAX_FILE_SIZE_MB = 500;

// Application state
const state = {
    sessionId: null,
    currentView: 'landing',  // 'landing' | 'processing' | 'timeline' | 'detail'
    selectedFile: null,
    currentEraId: null
};

// Utility: Fetch wrapper with timeout (detailed in Step 6.5)
async function apiFetch(url, options = {}) {
    // Implementation in Step 6.5
}

// View management with smooth transitions
function showView(viewName) {
    const oldView = document.querySelector('.view:not(.hidden)');
    const newView = document.getElementById(`${viewName}-view`);
    
    // Fade out old view
    if (oldView) {
        oldView.classList.add('view-exit');
        setTimeout(() => {
            oldView.classList.add('hidden');
            oldView.classList.remove('view-exit');
        }, 300); // Match CSS transition duration
    }
    
    // Fade in new view
    newView.classList.remove('hidden');
    newView.classList.add('view-enter');
    
    // Trigger animations
    setTimeout(() => {
        newView.classList.remove('view-enter');
    }, 50);
    
    state.currentView = viewName;
    
    // Scroll to top smoothly
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// All DOM interactions wrapped in DOMContentLoaded (Steps 6.3-6.4)
document.addEventListener('DOMContentLoaded', () => {
    // DOM elements, event handlers, etc. will go here
});
```

### Step 6.1 — Create HTML Structure
In `index.html`:
- Add viewport meta tag for mobile
- Add views container with `landing-view`, `processing-view`, `timeline-view`, `detail-view` sections
- Each view has class `view` and all except landing have class `hidden`
- Landing view contains:
  - Title: "Spotify Eras"
  - Subtitle: "Discover your personal music timeline"
  - File upload area (drag-and-drop + click) with id `upload-area`
  - Hidden file input with id `file-input`, accepts `.json,.zip`
  - File info display (hidden by default) with id `file-info`
  - "Analyze My Music" button with id `analyze-btn`, disabled by default
  - Brief instructions on getting Spotify data export
  - Privacy note: "Your data is processed in-memory and never stored"
  - Footer with credits
- Error display area with id `error-message` (hidden by default)

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spotify Eras</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div id="app">
        <!-- Landing View -->
        <section id="landing-view" class="view">
            <div class="container">
                <h1>Spotify Eras</h1>
                <p class="subtitle">Discover your personal music timeline</p>

                <div id="upload-area" class="upload-area">
                    <p>Drag & drop your Spotify data here</p>
                    <p class="small">or click to browse</p>
                    <input type="file" id="file-input" accept=".json,.zip" hidden>
                </div>

                <div id="file-info" class="file-info hidden">
                    <span id="file-name"></span>
                    <button id="clear-file" class="btn-small">Remove</button>
                </div>

                <button id="analyze-btn" class="btn-primary" disabled>Analyze My Music</button>

                <div id="error-message" class="error hidden"></div>

                <div class="instructions">
                    <p>How to get your data:</p>
                    <ol>
                        <li>Go to <a href="https://www.spotify.com/account/privacy/" target="_blank" rel="noopener noreferrer">Spotify Privacy Settings</a></li>
                        <li>Request "Extended streaming history"</li>
                        <li>Wait for email (up to 30 days)</li>
                        <li>Upload the ZIP file here</li>
                    </ol>
                </div>

                <p class="privacy-note">Your data is processed in-memory and never stored permanently.</p>
            </div>
        </section>

        <!-- Processing View (Phase 7) -->
        <section id="processing-view" class="view hidden"></section>

        <!-- Timeline View (Phase 8) -->
        <section id="timeline-view" class="view hidden"></section>

        <!-- Detail View (Phase 9) -->
        <section id="detail-view" class="view hidden"></section>
    </div>
    <script src="app.js"></script>
</body>
</html>
```

### Step 6.2 — Style Landing Page
In `styles.css`:
- CSS reset and box-sizing
- Dark theme: background `#121212`, accent `#1DB954` (Spotify green), text `#FFFFFF`
- Secondary text color `#B3B3B3`
- Error color `#E91429`
- Centered layout with `max-width: 600px` and padding
- Upload area: dashed border `#535353`, border-radius, hover/drag-over state changes border to accent
- `.hidden` class with `display: none`
- Button styles: primary (green bg), disabled state (opacity 0.5, cursor not-allowed)
- Mobile-first with base styles working at 320px+

```css
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

:root {
    --bg-primary: #121212;
    --bg-secondary: #181818;
    --accent: #1DB954;
    --text-primary: #FFFFFF;
    --text-secondary: #B3B3B3;
    --error: #E91429;
    --border: #535353;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    min-height: 100vh;
    line-height: 1.5;
}

.container {
    max-width: 600px;
    margin: 0 auto;
    padding: 2rem 1rem;
    text-align: center;
}

h1 {
    font-size: 2.5rem;
    margin-bottom: 0.5rem;
}

.subtitle {
    color: var(--text-secondary);
    margin-bottom: 2rem;
}

.upload-area {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 3rem 2rem;
    cursor: pointer;
    transition: border-color 0.2s, background-color 0.2s;
}

.upload-area:hover,
.upload-area.drag-over {
    border-color: var(--accent);
    background-color: rgba(29, 185, 84, 0.1);
}

.upload-area .small {
    color: var(--text-secondary);
    font-size: 0.875rem;
    margin-top: 0.5rem;
}

.file-info {
    margin: 1rem 0;
    padding: 0.75rem;
    background: var(--bg-secondary);
    border-radius: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.btn-primary {
    background: var(--accent);
    color: var(--bg-primary);
    border: none;
    padding: 1rem 2rem;
    font-size: 1rem;
    font-weight: 600;
    border-radius: 50px;
    cursor: pointer;
    margin: 1.5rem 0;
    transition: transform 0.1s, opacity 0.2s;
}

.btn-primary:hover:not(:disabled) {
    transform: scale(1.02);
}

.btn-primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-small {
    background: transparent;
    color: var(--text-secondary);
    border: 1px solid var(--border);
    padding: 0.25rem 0.75rem;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.875rem;
}

.error {
    color: var(--error);
    background: rgba(233, 20, 41, 0.1);
    padding: 1rem;
    border-radius: 8px;
    margin: 1rem 0;
}

.hidden {
    display: none !important;
}

.instructions {
    text-align: left;
    margin: 2rem 0;
    padding: 1rem;
    background: var(--bg-secondary);
    border-radius: 8px;
}

.instructions ol {
    margin-left: 1.5rem;
    color: var(--text-secondary);
}

.instructions a {
    color: var(--accent);
    text-decoration: none;
    border-bottom: 1px solid transparent;
    transition: border-color 0.2s;
}

.instructions a:hover {
    border-bottom-color: var(--accent);
}

.privacy-note {
    color: var(--text-secondary);
    font-size: 0.875rem;
}

/* ========================================
   PREMIUM ANIMATIONS & TRANSITIONS
   Apple/Spotify-Wrapped Inspired
   ======================================== */

/* Smooth scrolling */
html {
    scroll-behavior: smooth;
}

/* View transitions with fade */
.view {
    opacity: 1;
    transform: translateY(0);
    transition: opacity 0.3s cubic-bezier(0.4, 0.0, 0.2, 1),
                transform 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
}

.view-enter {
    opacity: 0;
    transform: translateY(20px);
}

.view-exit {
    opacity: 0;
    transform: translateY(-10px);
}

/* Button press effect with scale */
.btn-primary:active:not(:disabled) {
    transform: scale(0.98);
}

.btn-primary:hover:not(:disabled) {
    transform: scale(1.03);
    box-shadow: 0 8px 30px rgba(29, 185, 84, 0.3);
}

.btn-secondary:active {
    transform: scale(0.97);
}

.btn-small:active {
    transform: scale(0.95);
}

/* Upload area breathing effect */
@keyframes breathe {
    0%, 100% {
        border-color: var(--border);
        background-color: transparent;
    }
    50% {
        border-color: rgba(29, 185, 84, 0.3);
        background-color: rgba(29, 185, 84, 0.03);
    }
}

.upload-area {
    transition: all 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
}

.upload-area:hover {
    animation: breathe 3s ease-in-out infinite;
}

/* File info slide-in */
.file-info {
    animation: slideInFromTop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes slideInFromTop {
    from {
        opacity: 0;
        transform: translateY(-20px) scale(0.95);
    }
    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}

/* Error shake animation */
.error {
    animation: shake 0.5s cubic-bezier(0.36, 0.07, 0.19, 0.97);
}

@keyframes shake {
    0%, 100% { transform: translateX(0); }
    10%, 30%, 50%, 70%, 90% { transform: translateX(-8px); }
    20%, 40%, 60%, 80% { transform: translateX(8px); }
}

/* Add subtle glassmorphism to cards */
.file-info,
.btn-primary {
    backdrop-filter: blur(10px);
}

/* Smooth focus states */
button:focus-visible,
.upload-area:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
    transition: outline-offset 0.2s ease;
}
```

### Step 6.3 — Implement File Selection Handler
In `app.js`, add file selection logic wrapped in DOMContentLoaded:

```javascript
// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const clearFileBtn = document.getElementById('clear-file');
    const analyzeBtn = document.getElementById('analyze-btn');
    const errorMessage = document.getElementById('error-message');

    // Drag-and-drop counter to prevent flickering on child elements
    let dragCounter = 0;

    // Click to upload
    uploadArea.addEventListener('click', () => fileInput.click());

    // Drag and drop
    uploadArea.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        uploadArea.classList.add('drag-over');
    });

    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    uploadArea.addEventListener('dragleave', () => {
        dragCounter--;
        if (dragCounter === 0) {
            uploadArea.classList.remove('drag-over');
        }
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        dragCounter = 0;
        uploadArea.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) handleFileSelect(file);
    });

    // File input change
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) handleFileSelect(file);
    });

    // Clear file
    clearFileBtn.addEventListener('click', () => {
        clearFile();
    });

    function formatFileSize(bytes) {
        if (bytes < 1024) {
            return `${bytes}B`;
        } else if (bytes < 1024 * 1024) {
            return `${(bytes / 1024).toFixed(1)}KB`;
        } else if (bytes < 1024 * 1024 * 1024) {
            return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
        } else {
            return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
        }
    }

    function handleFileSelect(file) {
        // Validate file type
        const validTypes = ['.json', '.zip'];
        const fileExt = file.name.toLowerCase().slice(file.name.lastIndexOf('.'));
        if (!validTypes.includes(fileExt)) {
            showError('Please upload a .json or .zip file');
            return;
        }

        // Validate file size
        const fileSizeMB = file.size / (1024 * 1024);
        if (fileSizeMB > MAX_FILE_SIZE_MB) {
            showError(`File too large. Maximum size is ${MAX_FILE_SIZE_MB}MB`);
            return;
        }

        // Store and display file
        state.selectedFile = file;
        fileName.textContent = `${file.name} (${formatFileSize(file.size)})`;
        fileInfo.classList.remove('hidden');
        uploadArea.classList.add('hidden');
        analyzeBtn.disabled = false;
        
        // Focus the analyze button for keyboard accessibility
        analyzeBtn.focus();
        
        hideError();
    }

    function clearFile() {
        state.selectedFile = null;
        fileInput.value = '';
        fileInfo.classList.add('hidden');
        uploadArea.classList.remove('hidden');
        analyzeBtn.disabled = true;
        hideError();
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
    }

    function hideError() {
        errorMessage.classList.add('hidden');
    }

    // Continue with Step 6.4 handlers inside this DOMContentLoaded block...
});
```

### Step 6.4 — Implement File Upload and Processing Trigger
Add upload handler inside the DOMContentLoaded block (continuing from Step 6.3):

```javascript
    // Inside DOMContentLoaded from Step 6.3...
    
    analyzeBtn.addEventListener('click', async () => {
        if (!state.selectedFile) return;

        // Save original button state
        const originalText = analyzeBtn.textContent;
        analyzeBtn.disabled = true;
        analyzeBtn.textContent = 'Uploading...';
        hideError();

        try {
            // Step 1: Upload file
            const formData = new FormData();
            formData.append('file', state.selectedFile);

            const uploadResponse = await apiFetch(`${API_URL}/upload`, {
                method: 'POST',
                body: formData
            });

            if (!uploadResponse.ok) {
                const data = await uploadResponse.json();
                throw new Error(data.error || 'Upload failed');
            }

            const { session_id } = await uploadResponse.json();
            state.sessionId = session_id;

            // Step 2: Transition to processing view
            showView('processing');

            // Step 3: Start processing (non-blocking)
            fetch(`${API_URL}/process/${session_id}`, { method: 'POST' })
                .catch(err => console.error('Process request failed:', err));

            // Step 4: Start listening for progress (implemented in Phase 7)
            startProgressListener(session_id);

        } catch (err) {
            // Reset UI to allow retry
            showError(err.message);
            analyzeBtn.disabled = false;
            analyzeBtn.textContent = originalText;
            
            // Optionally clear file selection on error
            // Uncomment if you want to force user to re-select file after error:
            // clearFile();
        }
    });

    // Placeholder for Phase 7
    function startProgressListener(sessionId) {
        console.log('Starting progress listener for:', sessionId);
        // Will be implemented in Phase 7
    }
    
    // End of DOMContentLoaded block from Step 6.3
});

### Step 6.5 — Handle Network Errors
Add global error handling for network failures. This should be defined BEFORE the DOMContentLoaded block so it's available globally:

```javascript
// Wrapper for fetch with timeout and error handling
// Define this at the top of app.js, before DOMContentLoaded
async function apiFetch(url, options = {}) {
    const timeout = options.timeout || 30000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        return response;
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            throw new Error('Request timed out. Please try again.');
        }
        throw new Error('Network error. Please check your connection.');
    }
}
```

**Note:** Use `apiFetch()` instead of plain `fetch()` for all API calls throughout the application for consistent error handling and timeout support.

---

## PHASE 7: Frontend — Processing Screen

### Step 7.1 — Create Processing View HTML
Update the processing section in `index.html`:

```html
<!-- Processing View -->
<section id="processing-view" class="view hidden">
    <div class="container">
        <h1>Analyzing Your Music</h1>
        <p id="stage-text" class="stage-text" role="status" aria-live="polite">Starting...</p>

        <div class="progress-container" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
            <div class="progress-bar">
                <div id="progress-fill" class="progress-fill" style="width: 0%"></div>
            </div>
            <span id="progress-percent" class="progress-percent">0%</span>
        </div>

        <div class="spinner" aria-label="Processing"></div>

        <p class="processing-note">This may take a minute for large libraries.</p>

        <div id="processing-error" class="error hidden" role="alert">
            <p id="processing-error-text"></p>
            <button id="retry-btn" class="btn-secondary">Try Again</button>
        </div>
    </div>
</section>
```

### Step 7.2 — Style Processing View
Add to `styles.css`:

```css
/* Processing View */
.stage-text {
    color: var(--text-secondary);
    margin-bottom: 2rem;
    min-height: 1.5em;
    transition: opacity 0.3s ease;
}

.stage-text.updating {
    opacity: 0.5;
}

.progress-container {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 2rem;
}

.progress-bar {
    flex: 1;
    height: 8px;
    background: var(--bg-secondary);
    border-radius: 4px;
    overflow: hidden;
}

.progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 4px;
    transition: width 0.3s ease;
}

.progress-percent {
    color: var(--text-secondary);
    font-size: 0.875rem;
    min-width: 3rem;
}

.spinner {
    width: 40px;
    height: 40px;
    border: 3px solid var(--bg-secondary);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 2rem auto;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

.processing-note {
    color: var(--text-secondary);
    font-size: 0.875rem;
}

.btn-secondary {
    background: transparent;
    color: var(--text-primary);
    border: 1px solid var(--border);
    padding: 0.75rem 1.5rem;
    font-size: 1rem;
    border-radius: 50px;
    cursor: pointer;
    margin-top: 1rem;
    transition: border-color 0.2s;
}

.btn-secondary:hover {
    border-color: var(--text-primary);
}
```

### Step 7.3 — Define Progress Stage Mapping
In `app.js`, add the stage text mapping. This should be defined outside the DOMContentLoaded block (near the top with other constants) since it's used by multiple functions:

```javascript
// Progress stage display text (add near top with other constants)
const STAGE_TEXT = {
    'uploading': 'Uploading your data...',
    'parsed': 'Reading your listening history...',
    'segmented': 'Detecting your music eras...',
    'naming': 'Generating era descriptions...',
    'named': 'Era descriptions complete...',
    'playlists': 'Building your playlists...',
    'complete': 'Done! Loading your timeline...',
    'error': 'Something went wrong'
};

function getStageText(stage) {
    return STAGE_TEXT[stage] || `Processing: ${stage}`;
}
```

### Step 7.4 — Implement SSE Progress Listener
Add the `startProgressListener` function that replaces the placeholder from Phase 6. This should be defined outside DOMContentLoaded (near other utility functions) so it's accessible globally:

```javascript
// SSE connection reference for cleanup
let eventSource = null;
let sseTimeoutId = null;

function startProgressListener(sessionId) {
    // Close any existing connection
    if (eventSource) {
        eventSource.close();
    }
    
    // Clear any existing timeout
    if (sseTimeoutId) {
        clearTimeout(sseTimeoutId);
    }

    const stageText = document.getElementById('stage-text');
    const progressFill = document.getElementById('progress-fill');
    const progressPercent = document.getElementById('progress-percent');
    const progressContainer = document.querySelector('.progress-container');
    const processingError = document.getElementById('processing-error');
    const processingErrorText = document.getElementById('processing-error-text');
    const spinner = document.querySelector('.spinner');

    // Set a client-side timeout (5 minutes) in case backend hangs
    sseTimeoutId = setTimeout(() => {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        spinner.classList.add('hidden');
        processingErrorText.textContent = 'Processing timed out. Please try again.';
        processingError.classList.remove('hidden');
    }, 5 * 60 * 1000);

    eventSource = new EventSource(`${API_URL}/progress/${sessionId}`);

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const { stage, percent, message } = data;

            // Update stage text with fade animation
            stageText.classList.add('updating');
            setTimeout(() => {
                stageText.textContent = getStageText(stage);
                stageText.classList.remove('updating');
            }, 150);

            // Prevent progress from going backwards
            const currentPercent = parseInt(progressFill.style.width) || 0;
            const newPercent = Math.max(currentPercent, percent || 0);
            progressFill.style.width = `${newPercent}%`;
            progressPercent.textContent = `${newPercent}%`;
            
            // Update ARIA attribute
            progressContainer.setAttribute('aria-valuenow', newPercent);

            // Handle completion
            if (stage === 'complete') {
                clearTimeout(sseTimeoutId);
                eventSource.close();
                eventSource = null;
                spinner.classList.add('hidden');
                loadTimeline();
            }

            // Handle error
            if (stage === 'error') {
                clearTimeout(sseTimeoutId);
                eventSource.close();
                eventSource = null;
                spinner.classList.add('hidden');
                processingErrorText.textContent = message || 'Processing failed. Please try again.';
                processingError.classList.remove('hidden');
            }
        } catch (err) {
            console.error('Failed to parse SSE message:', err);
        }
    };

    eventSource.onerror = (err) => {
        console.error('SSE connection error:', err);

        // Check if connection is permanently closed
        if (eventSource.readyState === EventSource.CLOSED) {
            clearTimeout(sseTimeoutId);
            
            // Only show error if we're still on processing view and no error is already shown
            if (state.currentView === 'processing' && processingError.classList.contains('hidden')) {
                spinner.classList.add('hidden');
                processingErrorText.textContent = 'Connection lost. Please try again.';
                processingError.classList.remove('hidden');
            }
        }
        // EventSource will auto-reconnect for transient errors
    };
}

// Cleanup function
function stopProgressListener() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (sseTimeoutId) {
        clearTimeout(sseTimeoutId);
        sseTimeoutId = null;
    }
}
```

### Step 7.5 — Implement Timeline Loading on Complete
Add function to fetch data and transition to timeline view. This should be defined outside DOMContentLoaded:

```javascript
async function loadTimeline() {
    try {
        // Fetch summary and eras in parallel using apiFetch for timeout handling
        const [summaryRes, erasRes] = await Promise.all([
            apiFetch(`${API_URL}/session/${state.sessionId}/summary`),
            apiFetch(`${API_URL}/session/${state.sessionId}/eras`)
        ]);

        if (!summaryRes.ok || !erasRes.ok) {
            throw new Error('Failed to load timeline data');
        }

        const summary = await summaryRes.json();
        const eras = await erasRes.json();

        // Store in state for use by timeline view
        state.summary = summary;
        state.eras = eras;

        // Transition to timeline
        showView('timeline');
        renderTimeline();  // Implemented in Phase 8

    } catch (err) {
        console.error('Failed to load timeline:', err);
        // Show error in processing view
        const processingError = document.getElementById('processing-error');
        const processingErrorText = document.getElementById('processing-error-text');
        const spinner = document.querySelector('.spinner');
        
        spinner.classList.add('hidden');
        processingErrorText.textContent = err.message || 'Failed to load your timeline. Please try again.';
        processingError.classList.remove('hidden');
    }
}

// Placeholder for Phase 8
function renderTimeline() {
    console.log('Rendering timeline with', state.eras.length, 'eras');
    // Will be implemented in Phase 8
}
```

### Step 7.6 — Implement Retry Handler
Add click handler for the retry button. This should be inside the DOMContentLoaded block since it accesses DOM elements and Phase 6 functions:

```javascript
// Inside DOMContentLoaded block, after other event handlers...

document.getElementById('retry-btn').addEventListener('click', () => {
    // Stop any active SSE connections to prevent race conditions
    stopProgressListener();
    
    // Reset processing view state
    const processingError = document.getElementById('processing-error');
    const spinner = document.querySelector('.spinner');
    const stageText = document.getElementById('stage-text');
    const progressFill = document.getElementById('progress-fill');
    const progressPercent = document.getElementById('progress-percent');
    const progressContainer = document.querySelector('.progress-container');

    processingError.classList.add('hidden');
    spinner.classList.remove('hidden');
    stageText.textContent = 'Starting...';
    progressFill.style.width = '0%';
    progressPercent.textContent = '0%';
    progressContainer.setAttribute('aria-valuenow', '0');

    // Go back to landing to re-upload
    showView('landing');

    // Reset landing view state (these functions are in the same DOMContentLoaded scope)
    clearFile();
    analyzeBtn.textContent = 'Analyze My Music';

    // Clear session and state
    state.sessionId = null;
    state.summary = null;
    state.eras = [];
});
```

### Step 7.7 — Update State Object
Go back to Step 6.0 and update the existing state object to include summary and eras for timeline use:

```javascript
// Application state (in Step 6.0)
const state = {
    sessionId: null,
    currentView: 'landing',
    selectedFile: null,
    currentEraId: null,
    summary: null,    // Added in Phase 7 for timeline
    eras: []          // Added in Phase 7 for timeline
};
```

**Note:** This modifies the state object that was originally defined in Step 6.0. Update that section to include these two new properties.

### Step 7.8 — Handle Page Unload
Clean up SSE connection if user leaves or refreshes:

```javascript
window.addEventListener('beforeunload', () => {
    stopProgressListener();
});
```

---

## PHASE 8: Frontend — Timeline View

### Step 8.1 — Create Timeline HTML Structure
Update the timeline section in `index.html`:

```html
<!-- Timeline View -->
<section id="timeline-view" class="view hidden">
    <div class="container timeline-container">
        <header class="timeline-header">
            <h1>Your Music Journey</h1>
            <div class="stats-grid" role="region" aria-label="Listening statistics">
                <div class="stat">
                    <span id="stat-hours" class="stat-value">0</span>
                    <span class="stat-label">hours listened</span>
                </div>
                <div class="stat">
                    <span id="stat-eras" class="stat-value">0</span>
                    <span class="stat-label">eras</span>
                </div>
                <div class="stat">
                    <span id="stat-artists" class="stat-value">0</span>
                    <span class="stat-label">artists</span>
                </div>
            </div>
            <p id="stat-date-range" class="date-range"></p>
        </header>

        <div id="timeline" class="timeline" role="region" aria-label="Music eras timeline">
            <!-- Era cards rendered here by JS -->
        </div>

        <button id="start-over-btn" class="btn-secondary">Start Over</button>
    </div>
</section>
```

### Step 8.2 — Style Timeline View
Add to `styles.css`:

```css
/* Timeline View */
.timeline-container {
    max-width: 700px;
    text-align: left;
}

.timeline-header {
    text-align: center;
    margin-bottom: 2rem;
    padding-bottom: 2rem;
    border-bottom: 1px solid var(--border);
    animation: fadeInDown 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes fadeInDown {
    from {
        opacity: 0;
        transform: translateY(-30px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.stats-grid {
    display: flex;
    justify-content: center;
    gap: 2rem;
    margin: 1.5rem 0;
}

.stat {
    text-align: center;
    animation: popIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) backwards;
}

.stat:nth-child(1) { animation-delay: 0.1s; }
.stat:nth-child(2) { animation-delay: 0.2s; }
.stat:nth-child(3) { animation-delay: 0.3s; }

@keyframes popIn {
    from {
        opacity: 0;
        transform: scale(0.8) translateY(20px);
    }
    to {
        opacity: 1;
        transform: scale(1) translateY(0);
    }
}

.stat-value {
    display: block;
    font-size: 2rem;
    font-weight: 700;
    color: var(--accent);
    background: linear-gradient(135deg, var(--accent) 0%, #15803d 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.stat-label {
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.date-range {
    color: var(--text-secondary);
    font-size: 0.875rem;
    animation: fadeIn 0.6s ease 0.4s backwards;
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

/* Timeline */
.timeline {
    position: relative;
    padding-left: 2rem;
}

.timeline::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(180deg, 
        transparent 0%,
        var(--border) 10%,
        var(--border) 90%,
        transparent 100%);
}

/* Era Card with premium animations */
.era-card {
    position: relative;
    background: linear-gradient(135deg, 
        var(--bg-secondary) 0%, 
        rgba(24, 24, 24, 0.95) 100%);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 16px;
    padding: 1.25rem;
    margin-bottom: 1.5rem;
    cursor: pointer;
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    opacity: 0;
    transform: translateX(-20px);
    animation: slideInFromLeft 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

/* Staggered animations for era cards */
.era-card:nth-child(1) { animation-delay: 0.1s; }
.era-card:nth-child(2) { animation-delay: 0.15s; }
.era-card:nth-child(3) { animation-delay: 0.2s; }
.era-card:nth-child(4) { animation-delay: 0.25s; }
.era-card:nth-child(5) { animation-delay: 0.3s; }
.era-card:nth-child(6) { animation-delay: 0.35s; }
.era-card:nth-child(7) { animation-delay: 0.4s; }
.era-card:nth-child(8) { animation-delay: 0.45s; }
.era-card:nth-child(9) { animation-delay: 0.5s; }
.era-card:nth-child(10) { animation-delay: 0.55s; }

@keyframes slideInFromLeft {
    from {
        opacity: 0;
        transform: translateX(-30px) scale(0.95);
    }
    to {
        opacity: 1;
        transform: translateX(0) scale(1);
    }
}

.era-card:hover {
    transform: translateX(8px) scale(1.02);
    box-shadow: 
        0 10px 40px rgba(0, 0, 0, 0.3),
        0 0 0 1px rgba(29, 185, 84, 0.1),
        0 0 30px rgba(29, 185, 84, 0.15);
    border-color: rgba(29, 185, 84, 0.2);
}

.era-card:active {
    transform: translateX(8px) scale(0.99);
}

.era-card::before {
    content: '';
    position: absolute;
    left: -2rem;
    top: 1.5rem;
    width: 14px;
    height: 14px;
    background: var(--accent);
    border-radius: 50%;
    transform: translateX(-6px);
    box-shadow: 0 0 0 4px var(--bg-primary),
                0 0 20px rgba(29, 185, 84, 0.4);
    transition: all 0.3s ease;
}

.era-card:hover::before {
    transform: translateX(-6px) scale(1.3);
    box-shadow: 0 0 0 6px var(--bg-primary),
                0 0 30px rgba(29, 185, 84, 0.6);
}

.era-card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 0.75rem;
}

.era-title {
    font-size: 1.25rem;
    font-weight: 600;
    margin: 0;
    background: linear-gradient(135deg, var(--text-primary) 0%, rgba(255, 255, 255, 0.8) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.era-dates {
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.era-duration {
    font-size: 0.75rem;
    color: var(--accent);
    background: rgba(29, 185, 84, 0.1);
    padding: 0.25rem 0.75rem;
    border-radius: 20px;
    border: 1px solid rgba(29, 185, 84, 0.2);
    white-space: nowrap;
}

.artist-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
}

.artist-tag {
    font-size: 0.75rem;
    color: var(--text-secondary);
    background: var(--bg-primary);
    padding: 0.25rem 0.75rem;
    border-radius: 50px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    transition: all 0.2s ease;
}

.era-card:hover .artist-tag {
    background: rgba(29, 185, 84, 0.1);
    border-color: rgba(29, 185, 84, 0.2);
    color: var(--accent);
}

.era-track-count {
    font-size: 0.75rem;
    color: var(--text-secondary);
    margin-top: 0.75rem;
}

#start-over-btn {
    display: block;
    margin: 2rem auto 0;
}

/* Mobile responsive styles */
@media (max-width: 600px) {
    .timeline-container {
        padding: 1rem 0.5rem;
    }

    .stats-grid {
        flex-direction: column;
        gap: 1rem;
    }

    .timeline {
        padding-left: 1.5rem;
    }

    .era-card {
        padding: 1rem;
    }

    .era-card::before {
        left: -1.5rem;
        width: 10px;
        height: 10px;
    }

    .era-card-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.5rem;
    }

    .artist-tags {
        gap: 0.375rem;
    }

    .artist-tag {
        font-size: 0.7rem;
        padding: 0.2rem 0.6rem;
    }
}
```

### Step 8.3 — Add Formatting Helper Functions
In `app.js`, add utility functions for formatting:

```javascript
// Formatting helpers
function formatDuration(ms) {
    const hours = Math.floor(ms / 3600000);
    if (hours >= 1000) {
        return `${(hours / 1000).toFixed(1)}k`;
    }
    return hours.toLocaleString();
}

function formatDateRange(startDate, endDate) {
    const options = { month: 'short', year: 'numeric' };
    const start = new Date(startDate).toLocaleDateString('en-US', options);
    const end = new Date(endDate).toLocaleDateString('en-US', options);
    return `${start} - ${end}`;
}

function formatEraDuration(startDate, endDate) {
    const start = new Date(startDate);
    const end = new Date(endDate);
    const days = Math.round((end - start) / (1000 * 60 * 60 * 24));

    if (days < 14) {
        return `${days} days`;
    } else if (days < 60) {
        const weeks = Math.round(days / 7);
        return `${weeks} week${weeks !== 1 ? 's' : ''}`;
    } else {
        const months = Math.round(days / 30);
        return `${months} month${months !== 1 ? 's' : ''}`;
    }
}
```

### Step 8.4 — Implement renderTimeline Function
Replace the placeholder `renderTimeline` function from Phase 7:

```javascript
function renderTimeline() {
    const { summary, eras } = state;

    // Update stats with animated numbers
    const hoursElement = document.getElementById('stat-hours');
    const erasElement = document.getElementById('stat-eras');
    const artistsElement = document.getElementById('stat-artists');
    
    const totalHours = formatDuration(summary.total_listening_time_ms);
    
    // Animate numbers (parse first if string contains formatting)
    setTimeout(() => {
        const hoursNum = parseInt(totalHours.replace(/,/g, ''));
        if (!isNaN(hoursNum)) {
            animateNumber(hoursElement, hoursNum, 1200);
        } else {
            hoursElement.textContent = totalHours;
        }
        
        animateNumber(erasElement, summary.total_eras, 1000);
        animateNumber(artistsElement, summary.total_artists, 1400);
    }, 200); // Delay to let view transition complete
    
    document.getElementById('stat-date-range').textContent =
        `${formatDateRange(summary.date_range.start, summary.date_range.end)}`;

    // Render era cards
    const timeline = document.getElementById('timeline');
    timeline.innerHTML = '';

    if (eras.length === 0) {
        timeline.innerHTML = '<p class="empty-state">No eras found in your listening history.</p>';
        return;
    }

    eras.forEach(era => {
        const card = createEraCard(era);
        timeline.appendChild(card);
    });
}

function createEraCard(era) {
    const card = document.createElement('div');
    card.className = 'era-card';
    card.dataset.eraId = era.id;
    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `View details for ${era.title} era`);

    const dateRange = formatDateRange(era.start_date, era.end_date);
    const duration = formatEraDuration(era.start_date, era.end_date);

    const artistTags = era.top_artists
        .map(a => `<span class="artist-tag">${escapeHtml(a.name)}</span>`)
        .join('');

    card.innerHTML = `
        <div class="era-card-header">
            <div>
                <h3 class="era-title">${escapeHtml(era.title)}</h3>
                <p class="era-dates">${dateRange}</p>
            </div>
            <span class="era-duration">${duration}</span>
        </div>
        <div class="artist-tags">${artistTags}</div>
        <p class="era-track-count">${era.playlist_track_count} tracks</p>
    `;

    // Click handler to view era details
    const handleActivate = () => {
        viewEraDetail(era.id);
    };

    card.addEventListener('click', handleActivate);
    
    // Keyboard navigation - Enter or Space activates
    card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleActivate();
        }
    });

    return card;
}

// HTML escaping for safety
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Animate number counting (Spotify Wrapped style)
function animateNumber(element, finalValue, duration = 1000) {
    const start = 0;
    const startTime = performance.now();
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Easing function (ease-out cubic)
        const easeOut = 1 - Math.pow(1 - progress, 3);
        const current = Math.floor(start + (finalValue - start) * easeOut);
        
        element.textContent = typeof finalValue === 'string' ? finalValue : current.toLocaleString();
        
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            element.textContent = typeof finalValue === 'string' ? finalValue : finalValue.toLocaleString();
        }
    }
    
    requestAnimationFrame(update);
}
```

### Step 8.5 — Implement Era Detail Navigation
Add function to transition to detail view (implemented in Phase 9). This should be defined outside DOMContentLoaded:

```javascript
async function viewEraDetail(eraId) {
    state.currentEraId = eraId;

    try {
        const response = await apiFetch(`${API_URL}/session/${state.sessionId}/eras/${eraId}`);

        if (!response.ok) {
            throw new Error('Failed to load era details');
        }

        const eraDetail = await response.json();
        state.currentEra = eraDetail;

        showView('detail');
        renderEraDetail();  // Implemented in Phase 9

    } catch (err) {
        console.error('Failed to load era detail:', err);
        showToast(err.message || 'Failed to load era details. Please try again.');
    }
}

// Placeholder for Phase 9
function renderEraDetail() {
    console.log('Rendering era detail:', state.currentEra);
    // Will be implemented in Phase 9
}
```

### Step 8.6 — Implement Start Over Handler
Add click handler for the start over button. This should be inside the DOMContentLoaded block since it accesses Phase 6 functions:

```javascript
// Inside DOMContentLoaded block, after other event handlers...

document.getElementById('start-over-btn').addEventListener('click', () => {
    // Reset all state
    state.sessionId = null;
    state.summary = null;
    state.eras = [];
    state.currentEraId = null;
    state.currentEra = null;

    // Reset landing view (these functions are in the same DOMContentLoaded scope)
    clearFile();
    analyzeBtn.textContent = 'Analyze My Music';

    // Go to landing
    showView('landing');
});
```

### Step 8.7 — Update State Object
Go back to Step 6.0 and update the existing state object to include currentEra for detail view:

```javascript
// Application state (in Step 6.0)
const state = {
    sessionId: null,
    currentView: 'landing',
    selectedFile: null,
    currentEraId: null,
    currentEra: null,     // Added in Phase 8 for detail view
    summary: null,        // Added in Phase 7
    eras: []              // Added in Phase 7
};
```

**Note:** This modifies the state object that was originally defined in Step 6.0 and updated in Step 7.7. Add the `currentEra` property to that existing object.

### Step 8.8 — Add Empty State Style
Add to `styles.css`:

```css
.empty-state {
    text-align: center;
    color: var(--text-secondary);
    padding: 3rem;
}
```

---

## PHASE 9: Frontend — Era Detail View

### Step 9.1 — Create Era Detail HTML Structure
Update the detail section in `index.html`:

```html
<!-- Detail View -->
<section id="detail-view" class="view hidden">
    <div class="container detail-container">
        <button id="back-btn" class="back-btn">
            <span class="back-arrow">←</span> Back to Timeline
        </button>

        <!-- Loading State -->
        <div id="detail-loading" class="detail-loading hidden" role="status" aria-live="polite">
            <div class="spinner" aria-label="Loading"></div>
            <p>Loading era details...</p>
        </div>

        <!-- Error State -->
        <div id="detail-error" class="detail-error hidden" role="alert">
            <p id="detail-error-text"></p>
            <button class="btn-secondary" onclick="showView('timeline')">Back to Timeline</button>
        </div>

        <!-- Content (shown after loading completes) -->
        <div id="detail-content">
            <header class="detail-header">
                <h1 id="detail-title" class="detail-title"></h1>
                <p id="detail-dates" class="detail-dates"></p>
                <p id="detail-summary" class="detail-summary"></p>
            </header>

            <div class="detail-stats">
                <div class="detail-stat">
                    <span id="detail-hours" class="detail-stat-value"></span>
                    <span class="detail-stat-label">hours</span>
                </div>
                <div class="detail-stat">
                    <span id="detail-track-count" class="detail-stat-value"></span>
                    <span class="detail-stat-label">tracks</span>
                </div>
            </div>

            <section class="detail-section">
                <h2>Top Artists</h2>
                <ol id="detail-artists" class="artist-list"></ol>
            </section>

            <section class="detail-section">
                <div class="section-header">
                    <h2>Playlist</h2>
                    <button id="copy-playlist-btn" class="btn-small">Copy Track List</button>
                </div>
                <ol id="detail-tracks" class="track-list"></ol>
            </section>
        </div>
    </div>

    <!-- Toast notification -->
    <div id="toast" class="toast hidden">Copied to clipboard!</div>
</section>
```

### Step 9.2 — Style Era Detail View
Add to `styles.css`:

```css
/* Detail View */
.detail-container {
    max-width: 700px;
    text-align: left;
}

.back-btn {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-size: 1rem;
    cursor: pointer;
    padding: 0.5rem 0;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    transition: color 0.2s;
}

.back-btn:hover {
    color: var(--text-primary);
}

.back-arrow {
    font-size: 1.25rem;
}

.detail-header {
    margin-bottom: 2rem;
}

.detail-title {
    font-size: 2rem;
    margin-bottom: 0.5rem;
}

.detail-dates {
    color: var(--text-secondary);
    margin-bottom: 1rem;
}

.detail-summary {
    color: var(--text-secondary);
    line-height: 1.6;
    font-size: 1.1rem;
}

.detail-stats {
    display: flex;
    gap: 2rem;
    margin-bottom: 2rem;
    padding: 1.5rem;
    background: var(--bg-secondary);
    border-radius: 12px;
}

.detail-stat {
    text-align: center;
}

.detail-stat-value {
    display: block;
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--accent);
}

.detail-stat-label {
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.detail-section {
    margin-bottom: 2rem;
}

.detail-section h2 {
    font-size: 1.25rem;
    margin-bottom: 1rem;
    color: var(--text-primary);
}

.section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
}

.section-header h2 {
    margin-bottom: 0;
}

/* Artist List */
.artist-list {
    list-style: none;
    padding: 0;
    margin: 0;
}

.artist-list li {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem 1rem;
    background: var(--bg-secondary);
    border-radius: 8px;
    margin-bottom: 0.5rem;
}

.artist-list .artist-name {
    font-weight: 500;
}

.artist-list .artist-plays {
    color: var(--text-secondary);
    font-size: 0.875rem;
}

/* Track List */
.track-list {
    list-style: none;
    padding: 0;
    margin: 0;
    counter-reset: track-counter;
}

.track-list li {
    display: flex;
    align-items: center;
    padding: 0.75rem 1rem;
    background: var(--bg-secondary);
    border-radius: 8px;
    margin-bottom: 0.5rem;
    counter-increment: track-counter;
}

.track-list li::before {
    content: counter(track-counter);
    min-width: 2rem;
    color: var(--text-secondary);
    font-size: 0.875rem;
}

.track-info {
    flex: 1;
    min-width: 0;
}

.track-name {
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.track-artist {
    color: var(--text-secondary);
    font-size: 0.875rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.track-plays {
    color: var(--text-secondary);
    font-size: 0.875rem;
    margin-left: 1rem;
    white-space: nowrap;
}

/* Toast Notification */
.toast {
    position: fixed;
    bottom: 2rem;
    left: 50%;
    transform: translateX(-50%);
    background: var(--accent);
    color: var(--bg-primary);
    padding: 0.75rem 1.5rem;
    border-radius: 8px;
    font-weight: 500;
    z-index: 1000;
    opacity: 0;
    transition: opacity 0.3s;
}

.toast.visible {
    opacity: 1;
}

.toast.hidden {
    display: block !important;
    opacity: 0;
    pointer-events: none;
}

/* Loading state for detail view */
.detail-loading {
    text-align: center;
    padding: 3rem;
    color: var(--text-secondary);
}

/* Error state for detail view */
.detail-error {
    text-align: center;
    padding: 3rem;
}

.detail-error p {
    color: var(--error);
    margin-bottom: 1rem;
}

/* Scrollable track list for many tracks */
.track-list-container {
    max-height: 500px;
    overflow-y: auto;
}

/* Mobile adjustments for detail view */
@media (max-width: 480px) {
    .detail-title {
        font-size: 1.5rem;
    }

    .detail-stats {
        gap: 1rem;
        padding: 1rem;
    }

    .detail-stat-value {
        font-size: 1.5rem;
    }

    .section-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.5rem;
    }

    .track-list li {
        padding: 0.5rem 0.75rem;
    }

    .track-plays {
        display: none;
    }
}
```

### Step 9.3 — Implement renderEraDetail Function
Replace the placeholder `renderEraDetail` function from Phase 8:

```javascript
function renderEraDetail() {
    const era = state.currentEra;

    // Update header
    document.getElementById('detail-title').textContent = era.title;
    document.getElementById('detail-dates').textContent = formatDateRange(era.start_date, era.end_date);
    document.getElementById('detail-summary').textContent = era.summary || '';

    // Update stats
    const hours = Math.floor(era.total_ms_played / 3600000);
    document.getElementById('detail-hours').textContent = hours.toLocaleString();
    document.getElementById('detail-track-count').textContent = era.top_tracks.length;

    // Render artists
    const artistList = document.getElementById('detail-artists');
    artistList.innerHTML = era.top_artists.map(artist => `
        <li>
            <span class="artist-name">${escapeHtml(artist.name)}</span>
            <span class="artist-plays">${artist.plays.toLocaleString()} plays</span>
        </li>
    `).join('');

    // Render tracks
    const trackList = document.getElementById('detail-tracks');
    trackList.innerHTML = era.top_tracks.map(track => `
        <li>
            <div class="track-info">
                <div class="track-name">${escapeHtml(track.track)}</div>
                <div class="track-artist">${escapeHtml(track.artist)}</div>
            </div>
            <span class="track-plays">${track.plays} plays</span>
        </li>
    `).join('');
}
```

### Step 9.4 — Implement Back Button Handler
Add click handler to return to timeline. This should be inside the DOMContentLoaded block:

```javascript
// Inside DOMContentLoaded block, after other event handlers...

document.getElementById('back-btn').addEventListener('click', () => {
    state.currentEraId = null;
    state.currentEra = null;
    showView('timeline');
});
```

### Step 9.5 — Implement Copy Playlist Feature
Add clipboard functionality with toast feedback. This should be inside the DOMContentLoaded block:

```javascript
// Inside DOMContentLoaded block, after other event handlers...

document.getElementById('copy-playlist-btn').addEventListener('click', async () => {
    const era = state.currentEra;
    if (!era || !era.top_tracks) return;

    // Format tracks as "Artist - Track" list
    const trackList = era.top_tracks
        .map((track, i) => `${i + 1}. ${track.artist} - ${track.track}`)
        .join('\n');

    const header = `${era.title}\n${formatDateRange(era.start_date, era.end_date)}\n\n`;
    const textToCopy = header + trackList;

    try {
        // Modern clipboard API
        await navigator.clipboard.writeText(textToCopy);
        showToast('Copied to clipboard!');
    } catch (err) {
        // Fallback for older browsers
        try {
            const textarea = document.createElement('textarea');
            textarea.value = textToCopy;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            const successful = document.execCommand('copy');
            document.body.removeChild(textarea);
            
            if (successful) {
                showToast('Copied to clipboard!');
            } else {
                throw new Error('Copy command failed');
            }
        } catch (fallbackErr) {
            console.error('Failed to copy:', fallbackErr);
            showToast('Failed to copy. Please copy manually.');
        }
    }
});
```

### Step 9.6 — Implement Toast Notification
Add toast display function. This should be defined outside DOMContentLoaded:

```javascript
// Toast timeout reference for cleanup
let toastTimeout = null;

function showToast(message, duration = 2000) {
    const toast = document.getElementById('toast');
    
    // Clear any existing timeout
    if (toastTimeout) {
        clearTimeout(toastTimeout);
        toastTimeout = null;
    }
    
    // Remove any existing visible state
    toast.classList.remove('visible');
    
    // Small delay to allow CSS transition reset
    setTimeout(() => {
        toast.textContent = message;
        toast.classList.remove('hidden');
        toast.classList.add('visible');

        toastTimeout = setTimeout(() => {
            toast.classList.remove('visible');
            setTimeout(() => {
                toast.classList.add('hidden');
                toastTimeout = null;
            }, 300);  // Wait for fade out transition
        }, duration);
    }, 50);
}
```

### Step 9.7 — Add Loading and Error States to Era Detail View
Update the `viewEraDetail` function from Phase 8 to use apiFetch and show loading/error states. This approach shows/hides elements instead of replacing HTML to preserve event listeners:

```javascript
async function viewEraDetail(eraId) {
    state.currentEraId = eraId;

    // Show detail view first
    showView('detail');
    
    // Show loading, hide content
    const loadingEl = document.getElementById('detail-loading');
    const contentEl = document.getElementById('detail-content');
    const errorEl = document.getElementById('detail-error');
    
    loadingEl.classList.remove('hidden');
    contentEl.classList.add('hidden');
    errorEl.classList.add('hidden');

    try {
        const response = await apiFetch(`${API_URL}/session/${state.sessionId}/eras/${eraId}`);

        if (!response.ok) {
            throw new Error('Failed to load era details');
        }

        const eraDetail = await response.json();
        state.currentEra = eraDetail;

        // Hide loading, show content
        loadingEl.classList.add('hidden');
        contentEl.classList.remove('hidden');
        
        renderEraDetail();

    } catch (err) {
        console.error('Failed to load era detail:', err);
        
        // Hide loading, show error
        loadingEl.classList.add('hidden');
        errorEl.classList.remove('hidden');
        document.getElementById('detail-error-text').textContent = 
            err.message || 'Failed to load era details. Please try again.';
    }
}

---

## PHASE 10: Shareable Cards

### Step 10.1 — Add Share Card Section to Detail View
Update the detail view HTML in `index.html` to include a share card section after the playlist:

```html
<!-- Add after the playlist section in detail-view -->
<section class="detail-section share-section">
    <div class="section-header">
        <h2>Share This Era</h2>
        <button id="download-card-btn" class="btn-small">Download Image</button>
    </div>

    <div id="share-card" class="share-card">
        <div class="share-card-inner">
            <div class="share-card-bg"></div>
            <div class="share-card-content">
                <p class="share-card-label">MY MUSIC ERA</p>
                <h2 id="share-card-title" class="share-card-title"></h2>
                <p id="share-card-dates" class="share-card-dates"></p>

                <div class="share-card-artists">
                    <p class="share-card-artists-label">Top Artists</p>
                    <ol id="share-card-artist-list"></ol>
                </div>

                <div class="share-card-stats">
                    <div class="share-card-stat">
                        <span id="share-card-hours" class="share-card-stat-value"></span>
                        <span class="share-card-stat-label">hours</span>
                    </div>
                    <div class="share-card-stat">
                        <span id="share-card-tracks" class="share-card-stat-value"></span>
                        <span class="share-card-stat-label">tracks</span>
                    </div>
                </div>

                <p class="share-card-branding">Spotify Eras</p>
            </div>
        </div>
    </div>

    <p class="share-hint">Screenshot or download to share!</p>
</div>
```

### Step 10.2 — Style the Share Card
Add to `styles.css`:

```css
/* Share Card Section */
.share-section {
    margin-top: 2rem;
    padding-top: 2rem;
    border-top: 1px solid var(--border);
}

.share-card {
    width: 100%;
    max-width: 400px;
    margin: 0 auto;
    aspect-ratio: 1 / 1;
    border-radius: 16px;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.share-card-inner {
    position: relative;
    width: 100%;
    height: 100%;
    padding: 2rem;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

/* Gradient background */
.share-card-bg {
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, #1DB954 0%, #191414 50%, #121212 100%);
    z-index: 0;
}

/* Add subtle pattern overlay */
.share-card-bg::after {
    content: '';
    position: absolute;
    inset: 0;
    background-image: radial-gradient(circle at 20% 80%, rgba(29, 185, 84, 0.3) 0%, transparent 50%),
                      radial-gradient(circle at 80% 20%, rgba(29, 185, 84, 0.2) 0%, transparent 40%);
}

.share-card-content {
    position: relative;
    z-index: 1;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.share-card-label {
    font-size: 0.75rem;
    letter-spacing: 0.2em;
    color: var(--accent);
    margin-bottom: 0.5rem;
    font-weight: 600;
}

.share-card-title {
    font-size: 1.75rem;
    font-weight: 700;
    margin: 0 0 0.25rem 0;
    line-height: 1.2;
}

.share-card-dates {
    font-size: 0.875rem;
    color: var(--text-secondary);
    margin-bottom: 1.5rem;
}

.share-card-artists {
    flex: 1;
}

.share-card-artists-label {
    font-size: 0.75rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
}

.share-card-artists ol {
    list-style: none;
    padding: 0;
    margin: 0;
}

.share-card-artists li {
    font-size: 1rem;
    padding: 0.25rem 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.share-card-artists li::before {
    content: counter(list-item);
    counter-increment: list-item;
    width: 1.5rem;
    height: 1.5rem;
    background: rgba(255, 255, 255, 0.1);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    color: var(--text-secondary);
}

.share-card-artists ol {
    counter-reset: list-item;
}

.share-card-stats {
    display: flex;
    gap: 2rem;
    margin-top: 1rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.share-card-stat {
    text-align: left;
}

.share-card-stat-value {
    display: block;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--accent);
}

.share-card-stat-label {
    font-size: 0.75rem;
    color: var(--text-secondary);
}

.share-card-branding {
    font-size: 0.75rem;
    color: var(--text-secondary);
    text-align: right;
    margin-top: 1rem;
    font-weight: 500;
}

.share-hint {
    text-align: center;
    color: var(--text-secondary);
    font-size: 0.875rem;
    margin-top: 1rem;
}

/* Mobile adjustments */
@media (max-width: 480px) {
    .share-card {
        max-width: 100%;
    }

    .share-card-inner {
        padding: 1.5rem;
    }

    .share-card-title {
        font-size: 1.5rem;
    }

    .share-card-artists li {
        font-size: 0.875rem;
    }
}
```

### Step 10.3 — Add html2canvas Library
Add the html2canvas script to `index.html` before your app.js:

```html
<!-- Add before </body> tag, before app.js -->
<script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
<script src="app.js"></script>
```

### Step 10.4 — Implement Share Card Rendering
Add function to populate the share card in `app.js`:

```javascript
function renderShareCard() {
    const era = state.currentEra;
    if (!era) return;

    // Populate card content
    document.getElementById('share-card-title').textContent = era.title;
    document.getElementById('share-card-dates').textContent = formatDateRange(era.start_date, era.end_date);

    // Top 5 artists
    const artistList = document.getElementById('share-card-artist-list');
    artistList.innerHTML = era.top_artists.slice(0, 5).map(artist =>
        `<li>${escapeHtml(artist.name)}</li>`
    ).join('');

    // Stats
    const hours = Math.floor(era.total_ms_played / 3600000);
    document.getElementById('share-card-hours').textContent = hours.toLocaleString();
    document.getElementById('share-card-tracks').textContent = era.top_tracks.length;
}
```

Update `renderEraDetail()` to call `renderShareCard()`:

```javascript
function renderEraDetail() {
    const era = state.currentEra;

    // ... existing code ...

    // Render share card
    renderShareCard();
}
```

### Step 10.5 — Implement Download Image Feature
Add the download handler:

```javascript
document.getElementById('download-card-btn').addEventListener('click', downloadShareCard);

async function downloadShareCard() {
    const card = document.getElementById('share-card');
    const btn = document.getElementById('download-card-btn');

    // Show loading state
    btn.disabled = true;
    btn.textContent = 'Generating...';

    try {
        // Configure html2canvas for better quality
        const canvas = await html2canvas(card, {
            scale: 2,  // 2x resolution for crisp images
            backgroundColor: '#121212',
            logging: false,
            useCORS: true,
            allowTaint: true
        });

        // Create download link
        const link = document.createElement('a');
        const eraTitle = state.currentEra.title.replace(/[^a-z0-9]/gi, '_').toLowerCase();
        link.download = `spotify-era-${eraTitle}.png`;
        link.href = canvas.toDataURL('image/png');
        link.click();

        showToast('Image downloaded!');
    } catch (err) {
        console.error('Failed to generate image:', err);
        showToast('Failed to generate image. Try taking a screenshot instead.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Download Image';
    }
}
```

### Step 10.6 — Update restoreDetailViewHTML for Share Card
Add share card HTML and event listener re-attachment to `restoreDetailViewHTML()`:

```javascript
function restoreDetailViewHTML() {
    const container = document.querySelector('.detail-container');
    container.innerHTML = `
        <!-- ... existing HTML from Step 9.8 ... -->

        <section class="detail-section share-section">
            <div class="section-header">
                <h2>Share This Era</h2>
                <button id="download-card-btn" class="btn-small">Download Image</button>
            </div>

            <div id="share-card" class="share-card">
                <div class="share-card-inner">
                    <div class="share-card-bg"></div>
                    <div class="share-card-content">
                        <p class="share-card-label">MY MUSIC ERA</p>
                        <h2 id="share-card-title" class="share-card-title"></h2>
                        <p id="share-card-dates" class="share-card-dates"></p>

                        <div class="share-card-artists">
                            <p class="share-card-artists-label">Top Artists</p>
                            <ol id="share-card-artist-list"></ol>
                        </div>

                        <div class="share-card-stats">
                            <div class="share-card-stat">
                                <span id="share-card-hours" class="share-card-stat-value"></span>
                                <span class="share-card-stat-label">hours</span>
                            </div>
                            <div class="share-card-stat">
                                <span id="share-card-tracks" class="share-card-stat-value"></span>
                                <span class="share-card-stat-label">tracks</span>
                            </div>
                        </div>

                        <p class="share-card-branding">Spotify Eras</p>
                    </div>
                </div>
            </div>

            <p class="share-hint">Screenshot or download to share!</p>
        </section>
    `;

    // Re-attach event listeners (existing ones + new)
    document.getElementById('back-btn').addEventListener('click', () => {
        state.currentEraId = null;
        state.currentEra = null;
        showView('timeline');
    });
    document.getElementById('copy-playlist-btn').addEventListener('click', copyPlaylistToClipboard);
    document.getElementById('download-card-btn').addEventListener('click', downloadShareCard);
}
```

### Step 10.7 — Add Alternative Gradient Themes (Optional)
Add color themes based on era characteristics:

```javascript
function getEraGradient(era) {
    // Simple hash based on era title for consistent colors
    const hash = era.title.split('').reduce((acc, char) => {
        return char.charCodeAt(0) + ((acc << 5) - acc);
    }, 0);

    const gradients = [
        'linear-gradient(135deg, #1DB954 0%, #191414 50%, #121212 100%)',  // Spotify green
        'linear-gradient(135deg, #667eea 0%, #191414 50%, #121212 100%)',  // Purple
        'linear-gradient(135deg, #f093fb 0%, #191414 50%, #121212 100%)',  // Pink
        'linear-gradient(135deg, #4facfe 0%, #191414 50%, #121212 100%)',  // Blue
        'linear-gradient(135deg, #fa709a 0%, #191414 50%, #121212 100%)',  // Coral
        'linear-gradient(135deg, #a8edea 0%, #191414 50%, #121212 100%)',  // Teal
    ];

    return gradients[Math.abs(hash) % gradients.length];
}

// Update renderShareCard to apply gradient
function renderShareCard() {
    const era = state.currentEra;
    if (!era) return;

    // Apply era-specific gradient
    const bg = document.querySelector('.share-card-bg');
    bg.style.background = getEraGradient(era);

    // ... rest of existing code ...
}
```

---

## PHASE 11: Polish & Error Handling

### Step 11.1 — Add Skeleton Loaders for Timeline
Add skeleton loading animation while timeline data loads. Add to `styles.css`:

```css
/* Skeleton Loading */
.skeleton {
    background: linear-gradient(
        90deg,
        var(--bg-secondary) 25%,
        #252525 50%,
        var(--bg-secondary) 75%
    );
    background-size: 200% 100%;
    animation: skeleton-loading 1.5s infinite;
    border-radius: 8px;
}

@keyframes skeleton-loading {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}

.skeleton-card {
    height: 120px;
    margin-bottom: 1.5rem;
    border-radius: 12px;
}

.skeleton-header {
    height: 2rem;
    width: 60%;
    margin-bottom: 1rem;
}

.skeleton-text {
    height: 1rem;
    width: 40%;
    margin-bottom: 0.5rem;
}

.skeleton-stat {
    height: 3rem;
    width: 80px;
}

.stats-skeleton {
    display: flex;
    justify-content: center;
    gap: 2rem;
    margin: 1.5rem 0;
}
```

Add skeleton rendering function to `app.js`:

```javascript
function showTimelineSkeleton() {
    const timeline = document.getElementById('timeline');
    timeline.innerHTML = `
        <div class="skeleton skeleton-card"></div>
        <div class="skeleton skeleton-card"></div>
        <div class="skeleton skeleton-card"></div>
    `;

    // Also show skeleton for stats
    document.getElementById('stat-hours').parentElement.innerHTML = '<div class="skeleton skeleton-stat"></div>';
    document.getElementById('stat-eras').parentElement.innerHTML = '<div class="skeleton skeleton-stat"></div>';
    document.getElementById('stat-artists').parentElement.innerHTML = '<div class="skeleton skeleton-stat"></div>';
}
```

### Step 11.2 — Add Comprehensive Error Messages
Create a centralized error message system in `app.js`:

```javascript
// Error message constants
const ERROR_MESSAGES = {
    // Upload errors
    FILE_TOO_LARGE: 'File is too large. Maximum size is 500MB.',
    INVALID_FILE_TYPE: 'Please upload a .json or .zip file from your Spotify data export.',
    UPLOAD_FAILED: 'Upload failed. Please check your connection and try again.',

    // Processing errors
    NO_LISTENING_DATA: 'No listening history found in this file. Make sure you uploaded your Spotify extended streaming history.',
    NO_ERAS_FOUND: 'We couldn\'t detect distinct music eras in your history. Try uploading more data or a longer time period.',
    PROCESSING_FAILED: 'Something went wrong while analyzing your data. Please try again.',
    LLM_FAILED: 'Couldn\'t generate era descriptions, but your timeline is ready.',

    // Network errors
    NETWORK_ERROR: 'Connection lost. Please check your internet and try again.',
    TIMEOUT: 'Request timed out. Please try again.',
    SESSION_EXPIRED: 'Your session has expired. Please upload your file again.',

    // Generic
    UNKNOWN: 'Something went wrong. Please try again.'
};

// Map backend error messages to user-friendly messages
function getUserFriendlyError(backendError) {
    const errorMap = {
        'No file provided': ERROR_MESSAGES.INVALID_FILE_TYPE,
        'Invalid file type': ERROR_MESSAGES.INVALID_FILE_TYPE,
        'No listening history found': ERROR_MESSAGES.NO_LISTENING_DATA,
        'No distinct eras found': ERROR_MESSAGES.NO_ERAS_FOUND,
        'Session not found': ERROR_MESSAGES.SESSION_EXPIRED,
        'Processing not complete': 'Still processing. Please wait...',
    };

    // Check if error contains any known substring
    for (const [key, value] of Object.entries(errorMap)) {
        if (backendError.includes(key)) {
            return value;
        }
    }

    return backendError || ERROR_MESSAGES.UNKNOWN;
}
```

### Step 11.3 — Add Error Display Component
Add a reusable error display component. Add to `index.html`:

```html
<!-- Add inside each view that needs error display -->
<div id="error-banner" class="error-banner hidden">
    <div class="error-banner-content">
        <span class="error-icon">!</span>
        <p id="error-banner-text"></p>
        <button id="error-banner-dismiss" class="error-dismiss">×</button>
    </div>
</div>
```

Add to `styles.css`:

```css
/* Error Banner */
.error-banner {
    position: fixed;
    top: 1rem;
    left: 50%;
    transform: translateX(-50%);
    z-index: 1000;
    width: calc(100% - 2rem);
    max-width: 500px;
}

.error-banner-content {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    background: var(--error);
    color: white;
    padding: 1rem;
    border-radius: 8px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.error-icon {
    font-size: 1.25rem;
    flex-shrink: 0;
    width: 1.5rem;
    height: 1.5rem;
    background: rgba(255, 255, 255, 0.2);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: bold;
}

.error-banner-content p {
    flex: 1;
    margin: 0;
    font-size: 0.875rem;
}

.error-dismiss {
    background: transparent;
    border: none;
    color: white;
    font-size: 1.5rem;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    opacity: 0.7;
}

.error-dismiss:hover {
    opacity: 1;
}

/* Animate in */
.error-banner.visible {
    animation: slideDown 0.3s ease;
}

@keyframes slideDown {
    from {
        opacity: 0;
        transform: translateX(-50%) translateY(-20px);
    }
    to {
        opacity: 1;
        transform: translateX(-50%) translateY(0);
    }
}
```

Add to `app.js`:

```javascript
function showErrorBanner(message, autoDismiss = true) {
    const banner = document.getElementById('error-banner');
    const text = document.getElementById('error-banner-text');

    text.textContent = getUserFriendlyError(message);
    banner.classList.remove('hidden');
    banner.classList.add('visible');

    if (autoDismiss) {
        setTimeout(() => {
            hideErrorBanner();
        }, 5000);
    }
}

function hideErrorBanner() {
    const banner = document.getElementById('error-banner');
    banner.classList.remove('visible');
    setTimeout(() => {
        banner.classList.add('hidden');
    }, 300);
}

// Dismiss button handler
document.getElementById('error-banner-dismiss').addEventListener('click', hideErrorBanner);
```

### Step 11.4 — Add Empty State for Timeline
Add empty state styling and component. Add to `styles.css`:

```css
/* Empty States */
.empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-secondary);
}

.empty-state-icon {
    font-size: 3rem;
    margin-bottom: 1rem;
    opacity: 0.5;
}

.empty-state-title {
    font-size: 1.25rem;
    color: var(--text-primary);
    margin-bottom: 0.5rem;
}

.empty-state-description {
    font-size: 0.875rem;
    max-width: 300px;
    margin: 0 auto 1.5rem;
    line-height: 1.5;
}
```

Update `renderTimeline()` in `app.js` to handle empty state:

```javascript
function renderTimeline() {
    const { summary, eras } = state;

    // Update stats
    document.getElementById('stat-hours').textContent = formatDuration(summary.total_listening_time_ms);
    document.getElementById('stat-eras').textContent = summary.total_eras;
    document.getElementById('stat-artists').textContent = summary.total_artists.toLocaleString();
    document.getElementById('stat-date-range').textContent =
        `${formatDateRange(summary.date_range.start, summary.date_range.end)}`;

    // Render era cards or empty state
    const timeline = document.getElementById('timeline');
    timeline.innerHTML = '';

    if (eras.length === 0) {
        timeline.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">*</div>
                <h3 class="empty-state-title">No Eras Detected</h3>
                <p class="empty-state-description">
                    We couldn't find distinct music eras in your listening history.
                    Try uploading data from a longer time period.
                </p>
                <button onclick="location.reload()" class="btn-secondary">Try Again</button>
            </div>
        `;
        return;
    }

    eras.forEach(era => {
        const card = createEraCard(era);
        timeline.appendChild(card);
    });
}
```

### Step 11.5 — Add Button Loading States
Add loading state styling. Add to `styles.css`:

```css
/* Button Loading States */
.btn-loading {
    position: relative;
    color: transparent !important;
    pointer-events: none;
}

.btn-loading::after {
    content: '';
    position: absolute;
    width: 1rem;
    height: 1rem;
    top: 50%;
    left: 50%;
    margin-left: -0.5rem;
    margin-top: -0.5rem;
    border: 2px solid transparent;
    border-top-color: currentColor;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}

.btn-primary.btn-loading::after {
    border-top-color: var(--bg-primary);
}

.btn-secondary.btn-loading::after {
    border-top-color: var(--text-primary);
}
```

Add helper function to `app.js`:

```javascript
function setButtonLoading(button, loading, originalText = null) {
    if (loading) {
        button.dataset.originalText = button.textContent;
        button.classList.add('btn-loading');
        button.disabled = true;
    } else {
        button.classList.remove('btn-loading');
        button.disabled = false;
        button.textContent = originalText || button.dataset.originalText || button.textContent;
    }
}
```

### Step 11.6 — Mobile Responsive Improvements
Add comprehensive mobile styles. Add to `styles.css`:

```css
/* Mobile Responsive - Base improvements */
@media (max-width: 480px) {
    /* Global */
    .container {
        padding: 1rem;
    }

    h1 {
        font-size: 1.75rem;
    }

    /* Landing */
    .upload-area {
        padding: 2rem 1rem;
    }

    .instructions ol {
        padding-left: 1rem;
        font-size: 0.875rem;
    }

    /* Processing */
    .progress-container {
        flex-direction: column;
        gap: 0.5rem;
    }

    .progress-percent {
        text-align: center;
    }

    /* Timeline */
    .timeline-header h1 {
        font-size: 1.5rem;
    }

    .stats-grid {
        gap: 1rem;
    }

    .stat-value {
        font-size: 1.5rem;
    }

    .timeline {
        padding-left: 1.5rem;
    }

    .era-card {
        padding: 1rem;
    }

    .era-card::before {
        left: -1.5rem;
        width: 10px;
        height: 10px;
    }

    .era-title {
        font-size: 1rem;
    }

    .era-card-header {
        flex-direction: column;
        gap: 0.5rem;
    }

    .artist-tags {
        gap: 0.25rem;
    }

    .artist-tag {
        font-size: 0.7rem;
        padding: 0.2rem 0.5rem;
    }

    /* Ensure tap targets are 44px minimum */
    .btn-primary,
    .btn-secondary,
    .btn-small {
        min-height: 44px;
        min-width: 44px;
    }

    .era-card {
        min-height: 44px;
    }

    .back-btn {
        min-height: 44px;
    }
}

/* Small mobile (320px) */
@media (max-width: 350px) {
    .stats-grid {
        flex-direction: column;
        gap: 0.75rem;
    }

    .stat {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
    }

    .stat-value {
        font-size: 1.25rem;
    }
}

/* Landscape mobile */
@media (max-height: 500px) and (orientation: landscape) {
    .container {
        padding: 0.5rem 1rem;
    }

    .upload-area {
        padding: 1.5rem;
    }
}
```

### Step 11.7 — Add Page Transition Animations
Add smooth transitions between views. Add to `styles.css`:

```css
/* View Transitions */
.view {
    opacity: 1;
    transition: opacity 0.2s ease;
}

.view.hidden {
    opacity: 0;
    pointer-events: none;
    position: absolute;
    visibility: hidden;
}

.view.fade-in {
    animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}
```

Update `showView()` function in `app.js`:

```javascript
function showView(viewName) {
    // Hide all views
    document.querySelectorAll('.view').forEach(v => {
        v.classList.add('hidden');
        v.classList.remove('fade-in');
    });

    // Show target view with animation
    const targetView = document.getElementById(`${viewName}-view`);
    targetView.classList.remove('hidden');
    targetView.classList.add('fade-in');

    // Scroll to top
    window.scrollTo(0, 0);

    state.currentView = viewName;
}
```

### Step 11.8 — Add Privacy Note Enhancement
Update the privacy note in landing view with expandable details. Update in `index.html`:

```html
<div class="privacy-section">
    <p class="privacy-note">
        <span class="privacy-icon">*</span>
        Your data is processed in-memory and never stored permanently.
    </p>
    <button id="privacy-toggle" class="privacy-toggle">Learn more</button>
    <div id="privacy-details" class="privacy-details hidden">
        <ul>
            <li>Your file is processed entirely in your browser session</li>
            <li>Data is automatically deleted after 1 hour of inactivity</li>
            <li>We don't store, sell, or share your listening history</li>
            <li>No account or login required</li>
        </ul>
    </div>
</div>
```

Add to `styles.css`:

```css
/* Privacy Section */
.privacy-section {
    margin-top: 2rem;
    text-align: center;
}

.privacy-note {
    color: var(--text-secondary);
    font-size: 0.875rem;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
}

.privacy-icon {
    font-size: 1rem;
}

.privacy-toggle {
    background: transparent;
    border: none;
    color: var(--accent);
    font-size: 0.75rem;
    cursor: pointer;
    padding: 0.25rem;
    margin-top: 0.25rem;
}

.privacy-toggle:hover {
    text-decoration: underline;
}

.privacy-details {
    text-align: left;
    background: var(--bg-secondary);
    border-radius: 8px;
    padding: 1rem;
    margin-top: 0.75rem;
    font-size: 0.875rem;
}

.privacy-details ul {
    margin: 0;
    padding-left: 1.25rem;
    color: var(--text-secondary);
}

.privacy-details li {
    margin-bottom: 0.5rem;
}

.privacy-details li:last-child {
    margin-bottom: 0;
}
```

Add to `app.js`:

```javascript
// Privacy toggle
document.getElementById('privacy-toggle').addEventListener('click', function() {
    const details = document.getElementById('privacy-details');
    const isHidden = details.classList.contains('hidden');

    details.classList.toggle('hidden');
    this.textContent = isHidden ? 'Hide details' : 'Learn more';
});
```

### Step 11.9 — Add Offline Detection
Detect and notify users when they go offline. Add to `app.js`:

```javascript
// Offline detection
window.addEventListener('online', () => {
    hideErrorBanner();
    showToast('You\'re back online!');
});

window.addEventListener('offline', () => {
    showErrorBanner('You\'re offline. Please check your internet connection.', false);
});

// Check on load
if (!navigator.onLine) {
    showErrorBanner('You\'re offline. Please check your internet connection.', false);
}
```

### Step 11.10 — Add Keyboard Navigation
Add keyboard shortcuts for power users. Add to `app.js`:

```javascript
// Keyboard navigation
document.addEventListener('keydown', (e) => {
    // Escape to go back
    if (e.key === 'Escape') {
        if (state.currentView === 'detail') {
            state.currentEraId = null;
            state.currentEra = null;
            showView('timeline');
        }
    }

    // Enter to submit on landing (when file selected)
    if (e.key === 'Enter' && state.currentView === 'landing' && state.selectedFile) {
        document.getElementById('analyze-btn').click();
    }
});

// Make era cards focusable and keyboard accessible
function createEraCard(era) {
    const card = document.createElement('div');
    card.className = 'era-card';
    card.dataset.eraId = era.id;
    card.tabIndex = 0;  // Make focusable
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `View details for ${era.title}`);

    // ... existing card content ...

    // Add keyboard handler
    card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            viewEraDetail(era.id);
        }
    });

    card.addEventListener('click', () => {
        viewEraDetail(era.id);
    });

    return card;
}
```

Add focus styles to `styles.css`:

```css
/* Focus States for Accessibility */
.era-card:focus {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
}

.btn-primary:focus,
.btn-secondary:focus,
.btn-small:focus {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
}

/* Remove default focus outline for mouse users */
.era-card:focus:not(:focus-visible),
button:focus:not(:focus-visible) {
    outline: none;
}
```

---

## PHASE 12: Testing & Validation

### Step 12.1 — Create Mock Spotify Data Generator
Create `backend/tests/generate_mock_data.py` to generate test data:

```python
import json
import random
from datetime import datetime, timedelta

# Sample artists with genres for realistic era simulation
ARTIST_POOLS = {
    'indie': ['Phoebe Bridgers', 'Bon Iver', 'Fleet Foxes', 'Big Thief', 'Sufjan Stevens'],
    'pop': ['Taylor Swift', 'Dua Lipa', 'Harry Styles', 'Olivia Rodrigo', 'The Weeknd'],
    'hip_hop': ['Kendrick Lamar', 'Tyler, the Creator', 'Frank Ocean', 'SZA', 'Baby Keem'],
    'rock': ['Arctic Monkeys', 'The Strokes', 'Tame Impala', 'Radiohead', 'The Black Keys'],
    'electronic': ['Fred again..', 'Disclosure', 'Jamie xx', 'Caribou', 'Four Tet'],
}

TRACKS_PER_ARTIST = {
    'Taylor Swift': ['Anti-Hero', 'Cruel Summer', 'Blank Space', 'All Too Well', 'Shake It Off'],
    'Phoebe Bridgers': ['Motion Sickness', 'Kyoto', 'I Know the End', 'Scott Street', 'Moon Song'],
    'Kendrick Lamar': ['HUMBLE.', 'Money Trees', 'Swimming Pools', 'DNA.', 'Alright'],
    # Add more as needed, or generate random track names
}

def generate_track_name(artist):
    """Get a track name for an artist, or generate one."""
    if artist in TRACKS_PER_ARTIST:
        return random.choice(TRACKS_PER_ARTIST[artist])
    return f"Track {random.randint(1, 100)}"

def generate_listening_history(
    start_date: datetime,
    end_date: datetime,
    events_per_day: int = 20,
    era_changes: int = 4
) -> list:
    """
    Generate mock Spotify listening history with distinct eras.

    Args:
        start_date: Start of listening history
        end_date: End of listening history
        events_per_day: Average listening events per day
        era_changes: Number of times to change dominant genre

    Returns:
        List of listening events in Spotify export format
    """
    events = []
    total_days = (end_date - start_date).days
    era_length = total_days // (era_changes + 1)

    genres = list(ARTIST_POOLS.keys())
    current_genre = random.choice(genres)

    current_date = start_date
    days_in_era = 0

    while current_date < end_date:
        # Change era periodically
        days_in_era += 1
        if days_in_era > era_length:
            days_in_era = 0
            # Switch to a different genre
            available_genres = [g for g in genres if g != current_genre]
            current_genre = random.choice(available_genres)

        # Generate events for this day
        num_events = random.randint(events_per_day - 10, events_per_day + 10)
        num_events = max(0, num_events)  # Some days might have no listening

        for _ in range(num_events):
            # 70% from current era genre, 30% random
            if random.random() < 0.7:
                artist = random.choice(ARTIST_POOLS[current_genre])
            else:
                all_artists = [a for pool in ARTIST_POOLS.values() for a in pool]
                artist = random.choice(all_artists)

            track = generate_track_name(artist)

            # Random time during the day
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            timestamp = current_date.replace(hour=hour, minute=minute)

            # Play duration: 30 seconds to 5 minutes (filter <30s in parser)
            ms_played = random.randint(30000, 300000)

            event = {
                "ts": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "master_metadata_track_name": track,
                "master_metadata_album_artist_name": artist,
                "ms_played": ms_played,
                "spotify_track_uri": f"spotify:track:{random.randint(10000, 99999)}"
            }
            events.append(event)

        current_date += timedelta(days=1)

    # Sort by timestamp
    events.sort(key=lambda e: e["ts"])
    return events

def generate_edge_case_data() -> list:
    """Generate data with edge cases for parser testing."""
    base_event = {
        "ts": "2023-06-15T14:30:00Z",
        "master_metadata_track_name": "Normal Track",
        "master_metadata_album_artist_name": "Normal Artist",
        "ms_played": 180000,
        "spotify_track_uri": "spotify:track:12345"
    }

    events = [base_event.copy()]

    # Edge case: null track name (should be filtered)
    events.append({
        **base_event,
        "ts": "2023-06-15T14:31:00Z",
        "master_metadata_track_name": None
    })

    # Edge case: null artist name (should be filtered)
    events.append({
        **base_event,
        "ts": "2023-06-15T14:32:00Z",
        "master_metadata_album_artist_name": None
    })

    # Edge case: very short play (should be filtered)
    events.append({
        **base_event,
        "ts": "2023-06-15T14:33:00Z",
        "ms_played": 5000  # 5 seconds
    })

    # Edge case: Unicode characters
    events.append({
        **base_event,
        "ts": "2023-06-15T14:34:00Z",
        "master_metadata_track_name": "Rosas",
        "master_metadata_album_artist_name": "La Oreja de Van Gogh"
    })

    # Edge case: Special characters
    events.append({
        **base_event,
        "ts": "2023-06-15T14:35:00Z",
        "master_metadata_track_name": "Rock'n'Roll & Blues",
        "master_metadata_album_artist_name": "AC/DC"
    })

    # Edge case: Very long track name
    events.append({
        **base_event,
        "ts": "2023-06-15T14:36:00Z",
        "master_metadata_track_name": "A" * 500,
        "master_metadata_album_artist_name": "Artist"
    })

    # Edge case: Empty string (should be filtered)
    events.append({
        **base_event,
        "ts": "2023-06-15T14:37:00Z",
        "master_metadata_track_name": ""
    })

    return events

def save_mock_data(filename: str, events: list):
    """Save events to JSON file."""
    with open(filename, 'w') as f:
        json.dump(events, f, indent=2)
    print(f"Saved {len(events)} events to {filename}")

if __name__ == "__main__":
    # Generate 1 year of listening data with ~5 eras
    start = datetime(2023, 1, 1)
    end = datetime(2023, 12, 31)

    events = generate_listening_history(start, end, events_per_day=25, era_changes=5)
    save_mock_data("mock_spotify_data.json", events)

    # Generate edge case data
    edge_cases = generate_edge_case_data()
    save_mock_data("edge_case_data.json", edge_cases)

    print(f"\nGenerated {len(events)} normal events and {len(edge_cases)} edge case events")
```

### Step 12.2 — Create Parser Unit Tests
Create `backend/tests/test_parser.py`:

```python
import pytest
import json
from datetime import datetime
from parser import parse_spotify_json, ParseError

class TestParseSpotifyJson:
    def test_valid_event(self):
        """Test parsing a valid listening event."""
        data = [{
            "ts": "2023-06-15T14:30:00Z",
            "master_metadata_track_name": "Test Track",
            "master_metadata_album_artist_name": "Test Artist",
            "ms_played": 180000,
            "spotify_track_uri": "spotify:track:12345"
        }]
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 1
        assert events[0].track_name == "Test Track"
        assert events[0].artist_name == "Test Artist"
        assert events[0].ms_played == 180000

    def test_filters_short_plays(self):
        """Test that plays under 30 seconds are filtered."""
        data = [
            {"ts": "2023-06-15T14:30:00Z", "master_metadata_track_name": "Short",
             "master_metadata_album_artist_name": "Artist", "ms_played": 5000},
            {"ts": "2023-06-15T14:31:00Z", "master_metadata_track_name": "Long",
             "master_metadata_album_artist_name": "Artist", "ms_played": 60000}
        ]
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 1
        assert events[0].track_name == "Long"

    def test_filters_null_track_name(self):
        """Test that events with null track names are filtered."""
        data = [
            {"ts": "2023-06-15T14:30:00Z", "master_metadata_track_name": None,
             "master_metadata_album_artist_name": "Artist", "ms_played": 60000},
            {"ts": "2023-06-15T14:31:00Z", "master_metadata_track_name": "Valid",
             "master_metadata_album_artist_name": "Artist", "ms_played": 60000}
        ]
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 1
        assert events[0].track_name == "Valid"

    def test_filters_null_artist_name(self):
        """Test that events with null artist names are filtered."""
        data = [
            {"ts": "2023-06-15T14:30:00Z", "master_metadata_track_name": "Track",
             "master_metadata_album_artist_name": None, "ms_played": 60000}
        ]
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 0

    def test_handles_unicode(self):
        """Test parsing unicode characters in track/artist names."""
        data = [{
            "ts": "2023-06-15T14:30:00Z",
            "master_metadata_track_name": "Cancion",
            "master_metadata_album_artist_name": "Bad Bunny",
            "ms_played": 180000
        }]
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 1
        assert "Bunny" in events[0].artist_name

    def test_deduplicates_events(self):
        """Test that duplicate events are removed."""
        event = {
            "ts": "2023-06-15T14:30:00Z",
            "master_metadata_track_name": "Track",
            "master_metadata_album_artist_name": "Artist",
            "ms_played": 180000
        }
        data = [event, event.copy()]  # Duplicate
        events = parse_spotify_json(json.dumps(data).encode())
        assert len(events) == 1

    def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises ParseError."""
        with pytest.raises(ParseError):
            parse_spotify_json(b"not valid json")

    def test_empty_array(self):
        """Test parsing empty array returns empty list."""
        events = parse_spotify_json(b"[]")
        assert events == []
```

### Step 12.3 — Create Segmentation Unit Tests
Create `backend/tests/test_segmentation.py`:

```python
import pytest
from datetime import datetime, date
from collections import Counter
from segmentation import (
    aggregate_by_week,
    calculate_similarity,
    detect_era_boundaries,
    build_eras,
    filter_eras,
    segment_listening_history
)
from models import ListeningEvent, WeekBucket

def make_event(ts_str, artist, track="Track", ms=60000):
    """Helper to create test events."""
    return ListeningEvent(
        timestamp=datetime.fromisoformat(ts_str.replace('Z', '+00:00')),
        artist_name=artist,
        track_name=track,
        ms_played=ms
    )

class TestAggregateByWeek:
    def test_groups_by_week(self):
        """Test events are grouped into correct weeks."""
        events = [
            make_event("2023-01-02T10:00:00Z", "Artist1"),  # Week 1
            make_event("2023-01-03T10:00:00Z", "Artist1"),  # Week 1
            make_event("2023-01-09T10:00:00Z", "Artist2"),  # Week 2
        ]
        weeks = aggregate_by_week(events)
        assert len(weeks) == 2

    def test_counts_artists(self):
        """Test artist play counts are correct."""
        events = [
            make_event("2023-01-02T10:00:00Z", "Artist1"),
            make_event("2023-01-02T11:00:00Z", "Artist1"),
            make_event("2023-01-02T12:00:00Z", "Artist2"),
        ]
        weeks = aggregate_by_week(events)
        assert weeks[0].artists["Artist1"] == 2
        assert weeks[0].artists["Artist2"] == 1

    def test_empty_events(self):
        """Test empty event list returns empty weeks."""
        weeks = aggregate_by_week([])
        assert weeks == []

class TestCalculateSimilarity:
    def test_identical_weeks(self):
        """Test identical weeks have similarity 1.0."""
        week = WeekBucket(
            week_key=(2023, 1),
            week_start=date(2023, 1, 2),
            artists=Counter({"Artist1": 10, "Artist2": 5}),
            tracks=Counter(),
            total_ms=100000
        )
        similarity = calculate_similarity(week, week)
        assert similarity == 1.0

    def test_completely_different_weeks(self):
        """Test completely different weeks have similarity 0.0."""
        week_a = WeekBucket(
            week_key=(2023, 1),
            week_start=date(2023, 1, 2),
            artists=Counter({"Artist1": 10}),
            tracks=Counter(),
            total_ms=100000
        )
        week_b = WeekBucket(
            week_key=(2023, 2),
            week_start=date(2023, 1, 9),
            artists=Counter({"Artist2": 10}),
            tracks=Counter(),
            total_ms=100000
        )
        similarity = calculate_similarity(week_a, week_b)
        assert similarity == 0.0

    def test_partial_overlap(self):
        """Test partially overlapping weeks."""
        week_a = WeekBucket(
            week_key=(2023, 1),
            week_start=date(2023, 1, 2),
            artists=Counter({"Artist1": 10, "Artist2": 5}),
            tracks=Counter(),
            total_ms=100000
        )
        week_b = WeekBucket(
            week_key=(2023, 2),
            week_start=date(2023, 1, 9),
            artists=Counter({"Artist2": 10, "Artist3": 5}),
            tracks=Counter(),
            total_ms=100000
        )
        similarity = calculate_similarity(week_a, week_b)
        assert 0 < similarity < 1

class TestDetectEraBoundaries:
    def test_gap_creates_boundary(self):
        """Test that >4 week gap creates era boundary."""
        weeks = [
            WeekBucket((2023, 1), date(2023, 1, 2), Counter({"A": 10}), Counter(), 1000),
            WeekBucket((2023, 10), date(2023, 3, 6), Counter({"A": 10}), Counter(), 1000),
        ]
        boundaries = detect_era_boundaries(weeks)
        assert 1 in boundaries  # Gap should create boundary at index 1

    def test_low_similarity_creates_boundary(self):
        """Test that low similarity creates boundary."""
        weeks = [
            WeekBucket((2023, 1), date(2023, 1, 2), Counter({"A": 10}), Counter(), 1000),
            WeekBucket((2023, 2), date(2023, 1, 9), Counter({"B": 10}), Counter(), 1000),
        ]
        boundaries = detect_era_boundaries(weeks, threshold=0.5)
        assert 1 in boundaries

class TestFilterEras:
    def test_filters_short_eras(self):
        """Test eras shorter than min_weeks are filtered."""
        from models import Era
        eras = [
            Era(1, date(2023, 1, 2), date(2023, 1, 8), [], [], 10000000, "", ""),
            Era(2, date(2023, 2, 1), date(2023, 4, 1), [], [], 10000000, "", ""),
        ]
        filtered = filter_eras(eras, min_weeks=2)
        assert len(filtered) == 1
        assert filtered[0].id == 1  # Renumbered

    def test_filters_low_listening_time(self):
        """Test eras with low listening time are filtered."""
        from models import Era
        eras = [
            Era(1, date(2023, 1, 2), date(2023, 2, 28), [], [], 1000, "", ""),  # < 1 hour
            Era(2, date(2023, 3, 1), date(2023, 4, 30), [], [], 10000000, "", ""),
        ]
        filtered = filter_eras(eras, min_ms=3600000)
        assert len(filtered) == 1
```

### Step 12.4 — Create LLM Service Tests
Create `backend/tests/test_llm_service.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from models import Era
from llm_service import build_era_prompt, validate_era_name, name_era

def make_test_era():
    """Create a test era with sample data."""
    return Era(
        id=1,
        start_date=date(2023, 3, 1),
        end_date=date(2023, 8, 31),
        top_artists=[("Taylor Swift", 156), ("Phoebe Bridgers", 89), ("Bon Iver", 45)],
        top_tracks=[
            ("Anti-Hero", "Taylor Swift", 45),
            ("Kyoto", "Phoebe Bridgers", 32),
            ("Holocene", "Bon Iver", 28)
        ],
        total_ms_played=169200000,  # ~47 hours
        title="",
        summary=""
    )

class TestBuildEraPrompt:
    def test_includes_date_range(self):
        """Test prompt includes formatted date range."""
        era = make_test_era()
        prompt = build_era_prompt(era)
        assert "Mar 2023" in prompt or "March 2023" in prompt

    def test_includes_artists(self):
        """Test prompt includes top artists."""
        era = make_test_era()
        prompt = build_era_prompt(era)
        assert "Taylor Swift" in prompt
        assert "Phoebe Bridgers" in prompt

    def test_includes_tracks(self):
        """Test prompt includes top tracks."""
        era = make_test_era()
        prompt = build_era_prompt(era)
        assert "Anti-Hero" in prompt

class TestValidateEraName:
    def test_valid_response(self):
        """Test valid response passes through."""
        era = make_test_era()
        response = {"title": "Indie Summer", "summary": "A reflective period."}
        result = validate_era_name(response, era)
        assert result["title"] == "Indie Summer"

    def test_missing_title_uses_fallback(self):
        """Test missing title falls back to default."""
        era = make_test_era()
        response = {"summary": "Some summary"}
        result = validate_era_name(response, era)
        assert "Era" in result["title"] or "Mar" in result["title"]

    def test_empty_title_uses_fallback(self):
        """Test empty title falls back to default."""
        era = make_test_era()
        response = {"title": "", "summary": "Some summary"}
        result = validate_era_name(response, era)
        assert len(result["title"]) > 0

    def test_truncates_long_title(self):
        """Test long titles are truncated."""
        era = make_test_era()
        response = {"title": "A" * 100, "summary": "Summary"}
        result = validate_era_name(response, era)
        assert len(result["title"]) <= 50

    def test_truncates_long_summary(self):
        """Test long summaries are truncated."""
        era = make_test_era()
        response = {"title": "Title", "summary": "A" * 1000}
        result = validate_era_name(response, era)
        assert len(result["summary"]) <= 500

class TestNameEra:
    @patch('llm_service.get_client')
    def test_returns_title_and_summary(self, mock_client):
        """Test successful LLM call returns title and summary."""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"title": "Test Era", "summary": "A test."}'
        mock_client.return_value.chat.completions.create.return_value = mock_response

        era = make_test_era()
        result = name_era(era)

        assert "title" in result
        assert "summary" in result

    @patch('llm_service.get_client')
    def test_fallback_on_error(self, mock_client):
        """Test fallback is used when LLM fails."""
        mock_client.return_value.chat.completions.create.side_effect = Exception("API Error")

        era = make_test_era()
        result = name_era(era)

        # Should still return valid title/summary
        assert "title" in result
        assert len(result["title"]) > 0
```

### Step 12.5 — Create API Integration Tests
Create `backend/tests/test_api.py`:

```python
import pytest
import json
from app import app, sessions

@pytest.fixture
def client():
    """Create test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
    # Cleanup sessions after each test
    sessions.clear()

@pytest.fixture
def mock_session(client):
    """Create a mock completed session."""
    from datetime import datetime, date
    from models import Era, Playlist

    session_id = "test-session-123"
    sessions[session_id] = {
        "events": [],
        "eras": [
            Era(1, date(2023, 1, 1), date(2023, 3, 31),
                [("Artist1", 100), ("Artist2", 50)],
                [("Track1", "Artist1", 30), ("Track2", "Artist2", 20)],
                36000000, "Test Era", "A test era summary.")
        ],
        "playlists": [
            Playlist(1, [{"track_name": "Track1", "artist_name": "Artist1"}])
        ],
        "stats": {
            "total_tracks": 100,
            "total_artists": 20,
            "total_ms": 36000000,
            "date_range": {"start": "2023-01-01", "end": "2023-03-31"}
        },
        "progress": {"stage": "complete", "percent": 100},
        "created_at": datetime.now(),
        "last_accessed": datetime.now()
    }
    return session_id

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        """Test health endpoint returns ok status."""
        response = client.get('/health')
        assert response.status_code == 200
        assert response.json == {"status": "ok"}

class TestSummaryEndpoint:
    def test_returns_summary(self, client, mock_session):
        """Test summary endpoint returns stats."""
        response = client.get(f'/session/{mock_session}/summary')
        assert response.status_code == 200
        data = response.json
        assert "total_eras" in data
        assert "date_range" in data
        assert data["total_eras"] == 1

    def test_404_for_missing_session(self, client):
        """Test 404 for non-existent session."""
        response = client.get('/session/nonexistent/summary')
        assert response.status_code == 404

class TestErasEndpoint:
    def test_returns_eras_list(self, client, mock_session):
        """Test eras endpoint returns list of eras."""
        response = client.get(f'/session/{mock_session}/eras')
        assert response.status_code == 200
        data = response.json
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "Test Era"

class TestEraDetailEndpoint:
    def test_returns_era_detail(self, client, mock_session):
        """Test era detail endpoint returns full era data."""
        response = client.get(f'/session/{mock_session}/eras/1')
        assert response.status_code == 200
        data = response.json
        assert data["title"] == "Test Era"
        assert data["summary"] == "A test era summary."
        assert "top_artists" in data
        assert "top_tracks" in data
        assert "playlist" in data

    def test_404_for_missing_era(self, client, mock_session):
        """Test 404 for non-existent era."""
        response = client.get(f'/session/{mock_session}/eras/999')
        assert response.status_code == 404

    def test_400_for_invalid_era_id(self, client, mock_session):
        """Test 400 for invalid era ID format."""
        response = client.get(f'/session/{mock_session}/eras/invalid')
        assert response.status_code == 400

class TestUploadEndpoint:
    def test_rejects_no_file(self, client):
        """Test upload rejects request without file."""
        response = client.post('/upload')
        assert response.status_code == 400
        assert "error" in response.json

    def test_rejects_invalid_file_type(self, client):
        """Test upload rejects non-JSON/ZIP files."""
        from io import BytesIO
        data = {'file': (BytesIO(b'not a valid file'), 'test.txt')}
        response = client.post('/upload', data=data, content_type='multipart/form-data')
        assert response.status_code == 400
```

### Step 12.6 — Create End-to-End Test Script
Create `backend/tests/e2e_test.py`:

```python
"""
End-to-end test script for Spotify Eras.
Run with: python e2e_test.py path/to/spotify_data.json
"""
import sys
import time
import requests
import json

BASE_URL = "http://localhost:5000"

def test_full_flow(file_path: str):
    """Test the complete user flow."""
    print(f"\n{'='*50}")
    print("SPOTIFY ERAS - END-TO-END TEST")
    print(f"{'='*50}\n")

    start_time = time.time()

    # Step 1: Health check
    print("1. Checking server health...")
    response = requests.get(f"{BASE_URL}/health")
    assert response.status_code == 200, f"Health check failed: {response.text}"
    print("   Server is healthy")

    # Step 2: Upload file
    print(f"\n2. Uploading file: {file_path}")
    with open(file_path, 'rb') as f:
        response = requests.post(
            f"{BASE_URL}/upload",
            files={"file": (file_path, f, "application/json")}
        )
    assert response.status_code == 200, f"Upload failed: {response.text}"
    session_id = response.json()["session_id"]
    print(f"   Session ID: {session_id}")

    # Step 3: Start processing
    print("\n3. Starting processing...")
    response = requests.post(f"{BASE_URL}/process/{session_id}")
    assert response.status_code == 200, f"Process failed: {response.text}"
    print(f"   Processing started")

    # Step 4: Poll progress (simplified - in production use SSE)
    print("\n4. Waiting for completion...")
    max_wait = 120  # 2 minutes max
    waited = 0
    while waited < max_wait:
        time.sleep(2)
        waited += 2

        # Check summary endpoint to see if complete
        response = requests.get(f"{BASE_URL}/session/{session_id}/summary")
        if response.status_code == 200:
            break
        elif response.status_code == 425:
            stage = response.json().get("stage", "unknown")
            print(f"   Stage: {stage}...")
        else:
            print(f"   Waiting... ({waited}s)")

    assert response.status_code == 200, f"Processing didn't complete: {response.text}"

    # Step 5: Get summary
    print("\n5. Fetching summary...")
    summary = response.json()
    print(f"   Total eras: {summary['total_eras']}")
    print(f"   Date range: {summary['date_range']['start']} to {summary['date_range']['end']}")
    print(f"   Total artists: {summary['total_artists']}")
    print(f"   Total tracks: {summary['total_tracks']}")
    hours = summary['total_listening_time_ms'] // 3600000
    print(f"   Listening time: {hours} hours")

    # Step 6: Get eras list
    print("\n6. Fetching eras...")
    response = requests.get(f"{BASE_URL}/session/{session_id}/eras")
    assert response.status_code == 200, f"Failed to get eras: {response.text}"
    eras = response.json()

    for era in eras:
        print(f"\n   Era {era['id']}: {era['title']}")
        print(f"   Date: {era['start_date']} to {era['end_date']}")
        top_artists = ", ".join([a['name'] for a in era['top_artists'][:3]])
        print(f"   Top artists: {top_artists}")

    # Step 7: Get detail for first era
    if eras:
        print(f"\n7. Fetching detail for era 1...")
        response = requests.get(f"{BASE_URL}/session/{session_id}/eras/1")
        assert response.status_code == 200, f"Failed to get era detail: {response.text}"
        detail = response.json()
        print(f"   Title: {detail['title']}")
        print(f"   Summary: {detail['summary'][:100]}...")
        print(f"   Top tracks: {len(detail['top_tracks'])}")
        if detail['playlist']:
            print(f"   Playlist tracks: {len(detail['playlist']['tracks'])}")

    # Done
    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"TEST COMPLETED SUCCESSFULLY")
    print(f"Total time: {elapsed:.1f} seconds")
    print(f"{'='*50}\n")

    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python e2e_test.py <path_to_spotify_data.json>")
        sys.exit(1)

    try:
        test_full_flow(sys.argv[1])
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
```

### Step 12.7 — Add pytest Configuration
Create `backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --tb=short
filterwarnings =
    ignore::DeprecationWarning
```

Add test dependencies to `requirements.txt`:
```
pytest
pytest-cov
```

### Step 12.8 — Create Test Runner Script
Create `backend/run_tests.sh`:

```bash
#!/bin/bash

echo "================================"
echo "Running Spotify Eras Tests"
echo "================================"

# Run unit tests with coverage
echo -e "\n--- Unit Tests ---"
python -m pytest tests/ -v --cov=. --cov-report=term-missing --ignore=tests/e2e_test.py

# Check if unit tests passed
if [ $? -ne 0 ]; then
    echo -e "\nUnit tests FAILED"
    exit 1
fi

echo -e "\n--- All Tests Passed! ---"
```

Make it executable: `chmod +x run_tests.sh`

### Step 12.9 — Manual Testing Checklist
Create `backend/tests/MANUAL_TEST_CHECKLIST.md`:

```markdown
# Manual Testing Checklist

## Before Testing
- [ ] Backend server running (`python app.py`)
- [ ] Frontend served (open `index.html` or run local server)
- [ ] Have Spotify export data ready (real or mock)

## Landing Page
- [ ] Page loads with correct styling
- [ ] Drag-and-drop zone highlights on hover
- [ ] Can click to select file
- [ ] Can drag and drop file
- [ ] File size displayed after selection
- [ ] Can remove selected file
- [ ] "Analyze" button disabled until file selected
- [ ] Invalid file types rejected with error
- [ ] Privacy toggle expands/collapses

## Processing Screen
- [ ] Progress bar animates smoothly
- [ ] Stage text updates appropriately
- [ ] Spinner visible during processing
- [ ] Error message shown if processing fails
- [ ] Retry button returns to landing
- [ ] Processing completes within 90 seconds (typical)

## Timeline View
- [ ] Summary stats displayed correctly
- [ ] All eras shown in chronological order
- [ ] Era cards show title, dates, top artists
- [ ] Cards have hover effect
- [ ] Clicking card navigates to detail
- [ ] "Start Over" button works

## Era Detail View
- [ ] Back button returns to timeline
- [ ] Era title and summary displayed
- [ ] Stats (hours, tracks) correct
- [ ] Top artists list complete
- [ ] Track list with play counts
- [ ] "Copy Track List" copies formatted list
- [ ] Toast notification appears on copy
- [ ] Share card renders correctly
- [ ] "Download Image" generates PNG

## Mobile Testing (375px width)
- [ ] All views fit on screen
- [ ] Text readable without zooming
- [ ] Buttons large enough to tap (44px+)
- [ ] No horizontal scrolling
- [ ] Share card scales appropriately

## Error Handling
- [ ] Network error shows banner
- [ ] Session expiry shows message
- [ ] Empty data shows appropriate message
- [ ] LLM failure still shows timeline

## Accessibility
- [ ] Can navigate with keyboard
- [ ] Escape key closes detail view
- [ ] Focus indicators visible
- [ ] Screen reader announces content
```

---

## Summary Checklist

- [ ] Phase 0: Project structure created
- [ ] Phase 1: File parsing works for JSON and ZIP
- [ ] Phase 2: Era segmentation produces reasonable results
- [ ] Phase 3: LLM generates creative names and summaries
- [ ] Phase 4: Playlists generated for each era
- [ ] Phase 5: All API endpoints functional
- [ ] Phase 6: Landing page with upload working
- [ ] Phase 7: Processing screen shows real progress
- [ ] Phase 8: Timeline displays all eras
- [ ] Phase 9: Era detail view shows full info
- [ ] Phase 10: Shareable cards look good
- [ ] Phase 11: Error handling complete
- [ ] Phase 12: Tested with real data
