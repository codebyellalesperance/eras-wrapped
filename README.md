# eras wrapped

an interactive spotify wrapped experience that segments your listening history into distinct "eras" based on your musical evolution.

built with python (flask), javascript, and the spotify api.

## features

- **smart segmentation**: uses algorithms to detect shifts in your music taste over time
- **era analysis**: names and describes each of your musical eras using ai
- **interactive timeline**: explore your history with a smooth, gesture-based ui
- **spotify integration**: creates playlists for each era and saves them to your account
- **personalized stats**: track your top artists, tracks, and listening time per era

## setup

you need a spotify developer account and python 3.8+ installed.

1. clone the repo
2. create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. install dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
4. create a `.env` file in the root directory (see `.env.example`) and add your credentials:
   - spotify client id & secret
   - openai api key (for era naming)
   - flask secret key

## running locally

start the backend server:
```bash
cd backend
python3 app.py
```

in a separate terminal, start the frontend:
```bash
cd frontend
python3 -m http.server 8000
```

open `http://localhost:8000` in your browser.

## testing

run the backend test suite:
```bash
cd backend
pytest
```

see `docs/testing.md` for full testing documentation.

## deployment

configured for production with gunicorn. see `docs/production.md` for the readiness checklist and deployment guide.

## license

mit
