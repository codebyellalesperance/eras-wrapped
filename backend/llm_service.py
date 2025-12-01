import os
import time
import functools

from models import Era

# LLM Configuration
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'openai')
LLM_TIMEOUT = int(os.getenv('LLM_TIMEOUT', '30'))

# Default models per provider
DEFAULT_MODELS = {
    'openai': 'gpt-4o-mini',
    'anthropic': 'claude-3-haiku-20240307'
}

LLM_MODEL = os.getenv('LLM_MODEL', DEFAULT_MODELS.get(LLM_PROVIDER, 'gpt-4o-mini'))

# Client cache
_client = None


def get_client():
    """Get or create the LLM client based on provider configuration."""
    global _client

    if _client is not None:
        return _client

    if LLM_PROVIDER == 'openai':
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        from openai import OpenAI
        _client = OpenAI(api_key=api_key, timeout=LLM_TIMEOUT)

    elif LLM_PROVIDER == 'anthropic':
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        from anthropic import Anthropic
        _client = Anthropic(api_key=api_key, timeout=LLM_TIMEOUT)

    else:
        raise ValueError(f"Unknown LLM provider: {LLM_PROVIDER}")

    return _client


def retry_with_backoff(max_retries=3, base_delay=1):
    """Decorator for retrying functions with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()

                    # Check if it's a retryable error
                    retryable = any(term in error_str for term in [
                        'rate limit', 'timeout', 'connection',
                        'server error', '500', '502', '503', '529'
                    ])

                    if not retryable or attempt == max_retries - 1:
                        raise

                    # Exponential backoff: 1s, 2s, 4s
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)

            raise last_exception

        return wrapper
    return decorator


def format_duration(days: int) -> str:
    """Format duration in days to human-readable string."""
    if days < 14:
        return f"{days} days"
    elif days < 60:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    else:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''}"


def build_era_prompt(era: Era) -> str:
    """
    Build a prompt for the LLM to name and summarize an era.

    Args:
        era: Era object with listening data

    Returns:
        Formatted prompt string
    """
    # Format date range
    start_month = era.start_date.strftime("%B %Y")
    end_month = era.end_date.strftime("%B %Y")
    if start_month == end_month:
        date_range = start_month
    else:
        date_range = f"{start_month} - {end_month}"

    # Calculate duration
    duration_days = (era.end_date - era.start_date).days + 1
    duration = format_duration(duration_days)

    # Format listening time
    hours = era.total_ms_played // 3600000
    listening_time = f"{hours} hour{'s' if hours != 1 else ''}"

    # Format top 5 artists
    artists_lines = []
    for i, (artist, count) in enumerate(era.top_artists[:5], 1):
        artists_lines.append(f"{i}. {artist} ({count} plays)")
    formatted_artists = "\n".join(artists_lines)

    # Format top 10 tracks
    tracks_lines = []
    for i, (track, artist, count) in enumerate(era.top_tracks[:10], 1):
        tracks_lines.append(f"{i}. {track} by {artist} ({count} plays)")
    formatted_tracks = "\n".join(tracks_lines)

    prompt = f"""You are analyzing someone's music listening history. Based on this era's data, create a creative title and summary.

Era: {date_range} ({duration})
Total listening time: {listening_time}

Top Artists:
{formatted_artists}

Top Tracks:
{formatted_tracks}

Create a JSON response with:
- "title": A creative, evocative 2-5 word title that captures the mood/vibe. Avoid generic titles like "Musical Journey", "Eclectic Mix", or "Summer Vibes".
- "summary": A 2-3 sentence summary describing the musical mood, themes, or story of this era.

Respond ONLY with valid JSON: {{"title": "...", "summary": "..."}}"""

    return prompt
