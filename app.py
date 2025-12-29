# ============================================
# CRITICAL: Eventlet monkey patch MUST BE FIRST
# ============================================
import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
import os
import uuid
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///app.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# ============================================
# Database Models
# ============================================
class Room(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    teacher_sid = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()
    print("✅ Database tables created")

# ============================================
# In-Memory State
# ============================================
rooms = {}
sessions = {}

def get_room(room_id):
    if room_id not in rooms:
        rooms[room_id] = {
            "teacher": None,
            "students": set()
        }
    return rooms[room_id]

# ============================================
# Socket.IO Core
# ============================================
@socketio.on("connect")
def on_connect():
    sessions[request.sid] = {}
    print(f"✅ Connected: {request.sid}")

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid

    for room_id, room in rooms.items():
        if room["teacher"] == sid:
            room["teacher"] = None
            for student_sid in room["students"]:
                emit("teacher-disconnected", room=student_sid)

        if sid in room["students"]:
            room["students"].remove(sid)
            if room["teacher"]:
                emit(
                    "student-left",
                    {"sid": sid},
                    room=room["teacher"]
                )

    sessions.pop(sid, None)
    print(f"❌ Disconnected: {sid}")

# ============================================
# Room Join Logic
# ============================================
@socketio.on("join-room")
def join(data):
    try:
        room_id = data.get("room")
        role = data.get("role")

        if not room_id or not role:
            emit("error", {"message": "Invalid room or role"})
            return

        room = get_room(room_id)
        join_room(room_id)

        if role == "teacher":
            if room["teacher"]:
                emit("error", {"message": "Teacher already exists"})
                return

            room["teacher"] = request.sid

            with app.app_context():
                room_db = Room.query.get(room_id)
                if not room_db:
                    room_db = Room(
                        id=room_id,
                        teacher_sid=request.sid
                    )
                    db.session.add(room_db)
                else:
                    room_db.teacher_sid = request.sid
                db.session.commit()

            emit("room-joined", {
                "role": "teacher",
                "room": room_id
            })

        elif role == "student":
            if not room["teacher"]:
                emit("error", {"message": "Teacher not connected"})
                return

            room["students"].add(request.sid)

            emit("room-joined", {
                "role": "student",
                "room": room_id,
                "teacher_sid": room["teacher"]
            })

            emit(
                "student-joined",
                {"sid": request.sid},
                room=room["teacher"]
            )

    except Exception as e:
        print(f"❌ join-room error: {e}")
        emit("error", {"message": "Server error"})

# ============================================
# WebRTC Signaling Relay ONLY
# ============================================
@socketio.on("rtc-offer")
def rtc_offer(data):
    emit("rtc-offer", data, room=data["target_sid"])

@socketio.on("rtc-answer")
def rtc_answer(data):
    emit("rtc-answer", data, room=data["target_sid"])

@socketio.on("rtc-ice-candidate")
def rtc_ice(data):
    emit("rtc-ice-candidate", data, room=data["target_sid"])

# ============================================
# Routes
# ============================================
@app.route("/")
def index():
    return render_template("index.html")

# Redirect hyphen version to underscore
@app.route("/live-meeting")
def live_meeting_redirect():
    return redirect(url_for("live_meeting"))

# Create meeting and redirect teacher
@app.route("/live_meeting")
def live_meeting():
    room_id = uuid.uuid4().hex[:8]
    return redirect(url_for("teacher", room_id=room_id))

# Join existing meeting
@app.route("/join", methods=["GET", "POST"])
def join_page():
    if request.method == "POST":
        room_id = request.form.get("room_id", "").strip()
        if room_id:
            return redirect(url_for("student", room_id=room_id))
        flash("Please enter a valid Room ID")
    return render_template("join.html")

@app.route("/teacher/<room_id>")
def teacher(room_id):
    return render_template("teacher_live.html", room_id=room_id)

@app.route("/student/<room_id>")
def student(room_id):
    return render_template("student_live.html", room_id=room_id)

# ============================================
# Health Check
# ============================================
@app.route("/health")
def health():
    return {
        "status": "ok",
        "rooms": len(rooms),
        "connections": len(sessions)
    }

# ============================================
# Run Server
# ============================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Server running on port {port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=True)
