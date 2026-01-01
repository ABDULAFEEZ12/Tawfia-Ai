import eventlet
eventlet.monkey_patch()

from flask import Flask
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = "railway"

@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def root():
    return "RAILWAY OK", 200

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*"
)

if __name__ == "__main__":
    socketio.run(app, debug=True)
