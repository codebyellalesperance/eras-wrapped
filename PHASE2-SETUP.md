# Phase 2: Spotify Integration - Quick Start Guide

## 🎯 What We're Building
Real Spotify login, personalized recommendations, and playlist creation!

## 📋 Step 1: Get Spotify API Credentials (5 min)

### Go to: https://developer.spotify.com/dashboard

1. **Login** with Spotify
2. **Click** "Create app"
3. **Fill in:**
   - Name: `TasteSwipe`
   - Description: `Daily music discovery`
   - Redirect URI: `http://localhost:5001/callback`
   - Check: Web API
4. **Save** and copy:
   - Client ID
   - Client Secret (click "Show")

## 📝 Step 2: Update .env

Add these to `/Users/ellalesperance/Desktop/ctrl+alt+create/eras-wrapped/.env`:

```
SPOTIFY_CLIENT_ID=paste_your_client_id_here
SPOTIFY_CLIENT_SECRET=paste_your_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:5001/callback
```

## ✅ Step 3: Tell Me When Ready!

Once you've added those to .env, tell me **"ready"** and I'll build:
- OAuth login button
- Real Spotify song recommendations  
- Playlist creation from your likes
- Full integration in ~1 hour

---

**Need help?** I can walk you through creating the Spotify app!
