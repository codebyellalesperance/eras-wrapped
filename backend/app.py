import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS

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


if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_ENV') == 'development', port=5000)
