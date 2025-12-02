#!/bin/bash

# Spotify Eras - Quick Start Script

echo "🎵 Starting Spotify Eras Application..."
echo ""

# Check if .env exists and has API key
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "Please create .env file with your OpenAI API key"
    exit 1
fi

if grep -q "ADD_YOUR_KEY_HERE" .env; then
    echo "⚠️  Warning: Please add your OpenAI API key to .env file"
    echo "Edit .env and replace 'ADD_YOUR_KEY_HERE' with your actual key"
    echo ""
    read -p "Press Enter once you've added your key, or Ctrl+C to cancel..."
fi

echo "✅ Starting backend server..."
cd backend
python3 app.py &
BACKEND_PID=$!
cd ..

echo "⏳ Waiting for backend to start..."
sleep 3

echo "✅ Starting frontend server..."
cd frontend
python3 -m http.server 8000 &
FRONTEND_PID=$!
cd ..

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 Spotify Eras is running!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📱 Frontend: http://localhost:8000"
echo "🔧 Backend:  http://localhost:5000"
echo "🧪 Health:   http://localhost:5000/health"
echo ""
echo "📂 Sample data available: sample-data.json"
echo ""
echo "Press Ctrl+C to stop both servers"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Wait for Ctrl+C
trap "echo ''; echo '🛑 Stopping servers...'; kill $BACKEND_PID $FRONTEND_PID; exit" INT
wait
