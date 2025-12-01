import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from uuid import uuid4
import time
import orjson
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS

from parser import parse_spotify_json, parse_spotify_zip, ParseError
from segmentation import segment_listening_history
from llm_service import name_all_eras
from playlist_builder import build_all_playlists

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

# CORS configuration
allowed_origins = os.getenv('ALLOWED_ORIGINS', '*').split(',')
CORS(app, origins=allowed_origins)

# In-memory session store
sessions = {}

# Session cleanup settings
SESSION_MAX_AGE = timedelta(hours=1)


def cleanup_old_sessions():
    """Remove sessions older than SESSION_MAX_AGE."""
    now = datetime.now()
    expired = [
        sid for sid, data in sessions.items()
        if now - data.get('created_at', now) > SESSION_MAX_AGE
    ]
    for sid in expired:
        del sessions[sid]


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


# ZIP magic bytes
ZIP_MAGIC = b'PK\x03\x04'


def is_zip_file(file_bytes):
    """Check if file is a ZIP by magic bytes."""
    return file_bytes[:4] == ZIP_MAGIC


def is_valid_file_type(file_bytes, filename):
    """Check if file is a valid ZIP or JSON file."""
    is_zip = is_zip_file(file_bytes)
    is_json_ext = filename.lower().endswith('.json')
    is_zip_ext = filename.lower().endswith('.zip')
    return is_zip or is_json_ext or is_zip_ext


@app.route('/upload', methods=['POST'])
def upload():
    cleanup_old_sessions()

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    file_bytes = file.read()

    if not is_valid_file_type(file_bytes, file.filename):
        return jsonify({"error": "Invalid file type. Please upload a .json or .zip file"}), 400

    session_id = str(uuid4())
    sessions[session_id] = {
        "events": [],
        "eras": [],
        "playlists": [],
        "progress": {"stage": "uploading", "percent": 0},
        "created_at": datetime.now()
    }

    # Parse the file
    try:
        if is_zip_file(file_bytes):
            events = parse_spotify_zip(file_bytes)
        else:
            events = parse_spotify_json(file_bytes)
    except ParseError as e:
        del sessions[session_id]
        return jsonify({"error": f"Failed to parse file: {e}"}), 400

    if not events:
        del sessions[session_id]
        return jsonify({"error": "No listening history found in file"}), 400

    sessions[session_id]["events"] = events
    sessions[session_id]["progress"] = {"stage": "parsed", "percent": 20}

    return jsonify({"session_id": session_id})


# SSE settings
SSE_POLL_INTERVAL = 0.5  # seconds
SSE_KEEPALIVE_INTERVAL = 15  # seconds
SSE_TIMEOUT = 300  # 5 minutes max


@app.route('/progress/<session_id>', methods=['GET'])
def progress(session_id):
    if session_id not in sessions:
        return jsonify({"error": "Session not found"}), 404

    def generate():
        start_time = time.time()
        last_keepalive = start_time

        while True:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > SSE_TIMEOUT:
                yield f"data: {orjson.dumps({'stage': 'error', 'message': 'Timeout'}).decode()}\n\n"
                break

            # Check if session still exists
            if session_id not in sessions:
                yield f"data: {orjson.dumps({'stage': 'error', 'message': 'Session expired'}).decode()}\n\n"
                break

            # Get current progress
            session = sessions[session_id]
            progress_data = session.get("progress", {"stage": "unknown", "percent": 0})

            # Send progress update
            yield f"data: {orjson.dumps(progress_data).decode()}\n\n"

            # Check if complete or error
            if progress_data.get("stage") in ("complete", "error"):
                break

            # Send keepalive if needed
            current_time = time.time()
            if current_time - last_keepalive >= SSE_KEEPALIVE_INTERVAL:
                yield ": keepalive\n\n"
                last_keepalive = current_time

            time.sleep(SSE_POLL_INTERVAL)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering
        }
    )


@app.route('/process/<session_id>', methods=['POST'])
def process(session_id):
    """Trigger era segmentation and LLM naming for a session."""
    if session_id not in sessions:
        return jsonify({"error": "Session not found"}), 404

    session = sessions[session_id]

    if not session.get("events"):
        return jsonify({"error": "No events to process"}), 400

    try:
        # Phase 1: Segmentation
        eras = segment_listening_history(session["events"])

        if not eras:
            session["progress"] = {
                "stage": "error",
                "message": "No distinct eras found in your listening history",
                "percent": 0
            }
            return jsonify({"error": "No distinct eras found"}), 400

        # Store eras and update progress
        session["eras"] = eras
        session["progress"] = {"stage": "segmented", "percent": 40}

        # Free memory by removing raw events
        del session["events"]

        # Phase 2: LLM Naming
        def update_progress(percent):
            session["progress"] = {"stage": "naming", "percent": percent}

        name_all_eras(eras, update_progress)
        session["progress"] = {"stage": "named", "percent": 70}

        # Phase 3: Playlist Generation
        session["progress"] = {"stage": "playlists", "percent": 80}
        try:
            playlists = build_all_playlists(eras)
            session["playlists"] = playlists
        except Exception:
            # Playlist generation failed, continue with empty playlists
            session["playlists"] = []

        session["progress"] = {"stage": "complete", "percent": 100}

        return jsonify({"status": "ok", "era_count": len(eras)})

    except Exception as e:
        session["progress"] = {
            "stage": "error",
            "message": str(e),
            "percent": 0
        }
        return jsonify({"error": f"Processing failed: {e}"}), 500


if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_ENV') == 'development', port=5000)
