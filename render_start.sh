#!/usr/bin/env bash
# render_start.sh

echo "🚀 Starting Tawfiq AI application..."

# Optionally create a non-root user for security (recommended by Render)
if [ -n "$RENDER" ]; then
  # Create a non-root user (e.g., 'appuser') and group
  groupadd -r appgroup && useradd -r -g appgroup -s /bin/false appuser
  
  # Change ownership of the app directory (adjust path if needed)
  chown -R appuser:appgroup /opt/render/project/src
  
  echo "✅ Created non-root user for execution."
fi

# Install dependencies from requirements.txt
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Check if eventlet is installed for WebSocket support
if ! python -c "import eventlet" &> /dev/null; then
    echo "⚠️ eventlet not found in virtualenv. Installing now..."
    pip install eventlet
fi

# The most important command: Start Gunicorn with the eventlet worker.
# The '--worker-class eventlet' flag is NON-NEGOTIABLE for Socket.IO/WebSockets.
# '-w 1' uses 1 worker. For WebSockets, you typically start with 1.
# '--bind 0.0.0.0:${PORT:-10000}' binds to the port Render provides.
# 'app:app' points to your Flask app instance (adjust if your file/module is named differently).
echo "🔧 Launching Gunicorn with eventlet worker..."
exec gunicorn --worker-class eventlet \
  -w 1 \
  --bind 0.0.0.0:${PORT:-10000} \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  --log-level info \
  app:app

echo "❌ Gunicorn failed to start."
