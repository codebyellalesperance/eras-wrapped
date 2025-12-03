"""
AI Enhancement Service
Uses OpenAI to analyze taste, generate playlist names, and provide insights
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))


def analyze_music_taste(liked_songs, disliked_songs):
    """
    Analyze user's music taste based on their swipes
    Returns insights about their preferences
    """
    if not liked_songs:
        return {
            'summary': "You haven't liked any songs yet!",
            'vibe': 'exploratory',
            'recommendations': []
        }
    
    # Prepare song data
    liked_list = ", ".join([f"{s['track']} by {s['artist']}" for s in liked_songs[:5]])
    disliked_list = ", ".join([f"{s['track']} by {s['artist']}" for s in disliked_songs[:3]]) if disliked_songs else "none"
    
    prompt = f"""Analyze this user's music taste based on their swipes today:

LIKED ({len(liked_songs)} songs):
{liked_list}

DISLIKED ({len(disliked_songs)} songs):
{disliked_list}

Provide a brief, fun analysis (2-3 sentences) of their music taste. Be specific and personalized. Format as JSON:
{{
  "summary": "short analysis here",
  "vibe": "one word describing their vibe (e.g., eclectic, chill, energetic, nostalgic)",
  "mood": "detected mood (e.g., upbeat, melancholic, adventurous)"
}}"""

    try:
        response = client.chat.completions.create(
            model=os.getenv('LLM_MODEL', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "You are a music taste analyst. Be fun, specific, and insightful."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
            max_tokens=200
        )
        
        import json
        result = json.loads(response.choices[0].message.content)
        return result
        
    except Exception as e:
        print(f"AI taste analysis failed: {e}")
        return {
            'summary': f"You loved {len(liked_songs)} songs today!",
            'vibe': 'music-lover',
            'mood': 'discovering'
        }


def generate_playlist_name(liked_songs, taste_analysis=None):
    """
    Generate a creative, personalized playlist name
    """
    if not liked_songs:
        return "My TasteSwipe Daylist"
    
    from datetime import datetime
    date_str = datetime.now().strftime('%B %d')
    
    # Prepare song context
    artists = list(set([s['artist'] for s in liked_songs[:5]]))
    artist_str = ", ".join(artists[:3])
    
    vibe = taste_analysis.get('vibe', 'eclectic') if taste_analysis else 'eclectic'
    mood = taste_analysis.get('mood', 'vibing') if taste_analysis else 'vibing'
    
    prompt = f"""Create a creative, catchy playlist name for these {len(liked_songs)} songs:

Artists: {artist_str}
Vibe: {vibe}
Mood: {mood}
Date: {date_str}

Make it:
- Short (3-5 words max)
- Memorable and creative
- Related to the music style
- No emojis

Examples: "Midnight Indie Sessions", "Sunday Morning Chill", "Electric Dance Energy"

Just respond with the playlist name, nothing else."""

    try:
        response = client.chat.completions.create(
            model=os.getenv('LLM_MODEL', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "You are a creative playlist naming expert."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
            max_tokens=20
        )
        
        name = response.choices[0].message.content.strip().strip('"')
        return name
        
    except Exception as e:
        print(f"AI playlist naming failed: {e}")
        return f"TasteSwipe Mix - {date_str}"


def generate_song_insight(song, user_context=None):
    """
    Generate a brief insight about why this song was recommended
    """
    prompt = f"""In one fun sentence, explain why "{song['track']}" by {song['artist']} might appeal to someone.

Focus on the vibe, energy, or unique qualities. Be specific and enthusiastic.
Max 15 words."""

    try:
        response = client.chat.completions.create(
            model=os.getenv('LLM_MODEL', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "You are a music recommender. Be brief and exciting."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=30
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        print(f"AI insight failed: {e}")
        return "A great track you might enjoy!"


def detect_session_mood(liked_songs, disliked_songs, swipe_speed=None):
    """
    Detect the user's mood based on swiping behavior
    """
    total_swipes = len(liked_songs) + len(disliked_songs)
    like_ratio = len(liked_songs) / total_swipes if total_swipes > 0 else 0
    
    if like_ratio > 0.7:
        return {
            'mood': 'open-minded',
            'message': "You're in an exploratory mood today! 🌟",
            'color': '#1DB954'
        }
    elif like_ratio < 0.3:
        return {
            'mood': 'selective',
            'message': "Picky today? That's okay! Quality over quantity. 🎯",
            'color': '#FFA500'
        }
    else:
        return {
            'mood': 'balanced',
            'message': "You're vibing with a balanced taste! ✨",
            'color': '#00D4FF'
        }
