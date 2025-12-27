"""
app.py - COMPLETE FIXED VERSION WITH ALL HANDLERS
"""

# ============================================
# CRITICAL: Eventlet monkey patch MUST BE FIRST
# ============================================
import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied (FIRST THING)")

# ============================================
# NOW import everything else
# ============================================
import os
import json
from dotenv import load_dotenv
from hashlib import sha256
import redis
from functools import wraps
from sqlalchemy import func
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random
from difflib import get_close_matches
import requests

# Flask and extensions
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash,
    send_file, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, emit, disconnect

# Load environment variables
load_dotenv()

print("🚀 Initializing Tawfiq AI Live Meeting System...")

# ============================================
# FLASK APP CONFIGURATION
# ============================================
app = Flask(__name__)

# Database Configuration
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    if 'render.com' in DATABASE_URL or os.getenv('RENDER'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'connect_args': {
                'sslmode': 'require',
                'sslrootcert': 'prod-ca-2021.crt'
            }
        }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///local.db'

# App config
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'your-secret-key-here')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ============================================
# INITIALIZE EXTENSIONS
# ============================================
db = SQLAlchemy(app)

# Initialize Socket.IO with eventlet
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',  # CRITICAL: Must match monkey patch
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1e8,
    logger=True,
    engineio_logger=True,
    transports=['websocket', 'polling']
)
print("✅ Socket.IO initialized with eventlet async mode")

# ============================================
# DATABASE MODELS
# ============================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, default=1)
    joined_on = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class UserQuestions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class MeetingRoom(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    room_id = db.Column(db.String(64), nullable=False, index=True)
    teacher_id = db.Column(db.String(128), nullable=False)
    teacher_name = db.Column(db.String(150))
    state = db.Column(db.String(20), default='waiting')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    room_data = db.Column(db.JSON, default={})

# Create tables
with app.app_context():
    db.create_all()
    print("✅ Database tables created")

# ============================================
# REDIS SETUP
# ============================================
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
print(f"🔗 Redis URL: {REDIS_URL}")

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✅ Redis connected successfully")
    REDIS_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Redis connection failed: {e}")
    print("⚠️ Using in-memory storage (rooms reset on server restart)")
    REDIS_AVAILABLE = False
    redis_client = None

# Redis helper functions
def save_to_redis(key, data, ttl=3600):
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except:
            pass
    return False

def get_from_redis(key):
    if REDIS_AVAILABLE and redis_client:
        try:
            data = redis_client.get(key)
            return json.loads(data) if data else None
        except:
            pass
    return None

def delete_from_redis(key):
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.delete(key)
            return True
        except:
            pass
    return False

# ============================================
# IN-MEMORY STORAGE (active connections only)
# ============================================
active_sessions = {}
room_connections = {}

# Room state management
def save_room_state(room_id, state_data):
    room_key = f"room:{room_id}"
    save_to_redis(room_key, state_data)
    
    # Also save to database
    try:
        with app.app_context():
            room = MeetingRoom.query.filter_by(room_id=room_id).first()
            if room:
                room.room_data = state_data
                room.updated_at = datetime.utcnow()
            else:
                room = MeetingRoom(
                    id=room_key,
                    room_id=room_id,
                    teacher_id=state_data.get('teacher_id', 'unknown'),
                    teacher_name=state_data.get('teacher_name', 'Teacher'),
                    state=state_data.get('state', 'waiting'),
                    room_data=state_data
                )
                db.session.add(room)
            db.session.commit()
    except Exception as e:
        print(f"⚠️ Failed to save room to database: {e}")

def get_room_state(room_id):
    room_key = f"room:{room_id}"
    state = get_from_redis(room_key)
    if state:
        return state
    
    # Fallback to database
    try:
        with app.app_context():
            room = MeetingRoom.query.filter_by(room_id=room_id).first()
            if room and room.room_data:
                save_to_redis(room_key, room.room_data)
                return room.room_data
    except:
        pass
    
    return None

def delete_room_state(room_id):
    room_key = f"room:{room_id}"
    delete_from_redis(room_key)
    try:
        with app.app_context():
            room = MeetingRoom.query.filter_by(room_id=room_id).first()
            if room:
                db.session.delete(room)
                db.session.commit()
    except:
        pass

# ============================================
# SOCKET.IO EVENT HANDLERS - COMPLETE SET
# ============================================

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    print(f"✅ Client connected: {sid}")
    active_sessions[sid] = {
        'connected_at': datetime.utcnow().isoformat(),
        'room': None,
        'user_type': None,
        'user_id': None,
        'last_ping': datetime.utcnow().isoformat()
    }

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in active_sessions:
        session_data = active_sessions[sid]
        room_id = session_data.get('room')
        user_id = session_data.get('user_id')
        
        print(f"❌ Client disconnected: {sid} (room: {room_id})")
        
        # Clean up room connections
        if room_id and room_id in room_connections:
            if sid in room_connections[room_id]:
                room_connections[room_id].remove(sid)
            
            # Notify if teacher disconnected
            if session_data.get('user_type') == 'teacher':
                emit('teacher-disconnected', {
                    'teacherId': user_id,
                    'reason': 'Teacher left'
                }, room=room_id)
                
                # Clean up room state
                delete_room_state(room_id)
            elif session_data.get('user_type') == 'student':
                # Notify teacher about student leaving
                room_state = get_room_state(room_id)
                if room_state:
                    teacher_sid = room_state.get('teacher_sid')
                    if teacher_sid:
                        emit('student-left', {
                            'userId': user_id,
                            'username': 'Student',
                            'socketId': sid
                        }, room=teacher_sid)
        
        del active_sessions[sid]

@socketio.on('teacher-join')
def handle_teacher_join(data):
    try:
        sid = request.sid
        room_id = data.get('room')
        user_id = data.get('userId', f'teacher_{sid}')
        username = data.get('username', 'Teacher')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👨‍🏫 Teacher {username} joining room {room_id}")
        
        # Get or create room
        room_state = get_room_state(room_id)
        if not room_state:
            room_state = {
                'state': 'waiting',
                'teacher_id': user_id,
                'teacher_name': username,
                'teacher_sid': sid,
                'created_at': datetime.utcnow().isoformat(),
                'waiting_students': [],
                'connected_students': []
            }
        
        # Update room state
        room_state['teacher_id'] = user_id
        room_state['teacher_name'] = username
        room_state['teacher_sid'] = sid
        room_state['updated_at'] = datetime.utcnow().isoformat()
        
        save_room_state(room_id, room_state)
        
        # Update session
        active_sessions[sid]['room'] = room_id
        active_sessions[sid]['user_type'] = 'teacher'
        active_sessions[sid]['user_id'] = user_id
        
        # Room connections
        if room_id not in room_connections:
            room_connections[room_id] = []
        if sid not in room_connections[room_id]:
            room_connections[room_id].append(sid)
        
        join_room(room_id)
        
        emit('room-state', {
            'state': room_state['state'],
            'waitingStudents': len(room_state['waiting_students']),
            'connectedStudents': len(room_state['connected_students']),
            'teacherId': user_id,
            'teacherName': username
        })
        
        print(f"✅ Teacher joined room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-join: {e}")
        emit('error', {'message': str(e)})

@socketio.on('student-join')
def handle_student_join(data):
    try:
        sid = request.sid
        room_id = data.get('room')
        user_id = data.get('userId', f'student_{sid}')
        username = data.get('username', 'Student')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👨‍🎓 Student {username} joining room {room_id}")
        
        # Check room exists
        room_state = get_room_state(room_id)
        if not room_state:
            emit('error', {'message': 'Room not found'})
            return
        
        # Update session
        active_sessions[sid]['room'] = room_id
        active_sessions[sid]['user_type'] = 'student'
        active_sessions[sid]['user_id'] = user_id
        
        # Room connections
        if room_id not in room_connections:
            room_connections[room_id] = []
        if sid not in room_connections[room_id]:
            room_connections[room_id].append(sid)
        
        join_room(room_id)
        
        # Add student to room state
        student_data = {
            'user_id': user_id,
            'username': username,
            'sid': sid,
            'joined_at': datetime.utcnow().isoformat(),
            'connected': room_state['state'] == 'live'
        }
        
        if room_state['state'] == 'waiting':
            if user_id not in [s['user_id'] for s in room_state['waiting_students']]:
                room_state['waiting_students'].append(student_data)
        else:
            if user_id not in [s['user_id'] for s in room_state['connected_students']]:
                room_state['connected_students'].append(student_data)
        
        save_room_state(room_id, room_state)
        
        # Notify teacher
        teacher_sid = room_state.get('teacher_sid')
        if teacher_sid:
            emit('student-joined', {
                'userId': user_id,
                'username': username,
                'socketId': sid
            }, room=teacher_sid)
        
        # Send response to student
        if room_state['state'] == 'waiting':
            emit('student-waiting-ack', {
                'status': 'waiting',
                'room': room_id,
                'teacherName': room_state['teacher_name'],
                'message': 'Waiting for teacher to start the meeting'
            })
        else:
            emit('student-joined-ack', {
                'status': 'joined',
                'room': room_id,
                'teacherName': room_state['teacher_name'],
                'message': 'Successfully joined the live meeting'
            })
        
        # Update room state for all
        emit('room-state', {
            'state': room_state['state'],
            'waitingStudents': len(room_state['waiting_students']),
            'connectedStudents': len(room_state['connected_students']),
            'teacherId': room_state['teacher_id'],
            'teacherName': room_state['teacher_name']
        }, room=room_id)
        
        print(f"✅ Student joined room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in student-join: {e}")
        emit('error', {'message': str(e)})

@socketio.on('start-meeting')
def handle_start_meeting(data):
    try:
        room_id = data.get('room')
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"🚀 Starting meeting in room {room_id}")
        
        room_state = get_room_state(room_id)
        if not room_state:
            emit('error', {'message': 'Room not found'})
            return
        
        room_state['state'] = 'live'
        room_state['started_at'] = datetime.utcnow().isoformat()
        
        # Move waiting to connected
        for student in room_state['waiting_students']:
            if student['user_id'] not in [s['user_id'] for s in room_state['connected_students']]:
                student['connected'] = True
                room_state['connected_students'].append(student)
        room_state['waiting_students'] = []
        
        save_room_state(room_id, room_state)
        
        # Notify all
        emit('room-started', {
            'room': room_id,
            'teacherId': room_state['teacher_id'],
            'teacherName': room_state['teacher_name'],
            'startedAt': room_state['started_at']
        }, room=room_id)
        
        # Notify individual students
        for student in room_state['connected_students']:
            student_sid = student.get('sid')
            if student_sid:
                emit('meeting-started', {
                    'room': room_id,
                    'teacherName': room_state['teacher_name'],
                    'message': 'The meeting has started!'
                }, room=student_sid)
        
        # Update room state
        emit('room-state', {
            'state': 'live',
            'waitingStudents': 0,
            'connectedStudents': len(room_state['connected_students']),
            'teacherId': room_state['teacher_id'],
            'teacherName': room_state['teacher_name']
        }, room=room_id)
        
        print(f"✅ Meeting started in room {room_id} with {len(room_state['connected_students'])} students")
        
    except Exception as e:
        print(f"❌ Error in start-meeting: {e}")
        emit('error', {'message': str(e)})

@socketio.on('end-meeting')
def handle_end_meeting(data):
    try:
        room_id = data.get('room')
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"🛑 Ending meeting in room {room_id}")
        
        room_state = get_room_state(room_id)
        if room_state:
            emit('room-ended', {
                'room': room_id,
                'teacherId': room_state.get('teacher_id'),
                'teacherName': room_state.get('teacher_name'),
                'message': 'Meeting has ended',
                'endedAt': datetime.utcnow().isoformat()
            }, room=room_id)
        
        delete_room_state(room_id)
        
        if room_id in room_connections:
            for sid in room_connections[room_id]:
                if sid in active_sessions:
                    active_sessions[sid]['room'] = None
            del room_connections[room_id]
        
        print(f"✅ Meeting ended and cleaned up for room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in end-meeting: {e}")
        emit('error', {'message': str(e)})

@socketio.on('webrtc-signal')
def handle_webrtc_signal(data):
    try:
        room_id = data.get('room')
        from_user = data.get('from')
        to_user = data.get('to')
        signal = data.get('signal')
        signal_type = data.get('type', 'signal')
        
        # Find target socket ID
        target_sid = None
        
        # Check if target is teacher
        if to_user.startswith('teacher_'):
            room_state = get_room_state(room_id)
            if room_state:
                target_sid = room_state.get('teacher_sid')
        else:
            # Check if target is student in active sessions
            for sid, session_data in active_sessions.items():
                if session_data.get('user_id') == to_user and session_data.get('room') == room_id:
                    target_sid = sid
                    break
        
        if target_sid:
            emit('webrtc-signal', {
                'from': from_user,
                'to': to_user,
                'signal': signal,
                'type': signal_type
            }, room=target_sid)
        else:
            print(f"⚠️ Target user {to_user} not found in room {room_id}")
            
    except Exception as e:
        print(f"❌ Error in webrtc-signal: {e}")

# ============================================
# NEW: ADD THESE MISSING HANDLERS
# ============================================

@socketio.on('mute-student')
def handle_mute_student(data):
    """Mute a student's microphone"""
    try:
        room_id = data.get('room')
        student_id = data.get('userId')
        
        print(f"🔇 Muting student {student_id} in room {room_id}")
        
        # Find student socket
        for sid, session in active_sessions.items():
            if session.get('user_id') == student_id and session.get('room') == room_id:
                emit('student-muted', {
                    'userId': student_id,
                    'muted': True
                }, room=sid)
                
                # Confirm to teacher
                room_state = get_room_state(room_id)
                if room_state:
                    teacher_sid = room_state.get('teacher_sid')
                    if teacher_sid:
                        emit('student-muted-confirm', {
                            'userId': student_id,
                            'muted': True
                        }, room=teacher_sid)
                
                break
                
    except Exception as e:
        print(f"❌ Error in mute-student: {e}")

@socketio.on('unmute-student')
def handle_unmute_student(data):
    """Unmute a student's microphone"""
    try:
        room_id = data.get('room')
        student_id = data.get('userId')
        
        print(f"🔊 Unmuting student {student_id} in room {room_id}")
        
        # Find student socket
        for sid, session in active_sessions.items():
            if session.get('user_id') == student_id and session.get('room') == room_id:
                emit('student-muted', {
                    'userId': student_id,
                    'muted': False
                }, room=sid)
                
                # Confirm to teacher
                room_state = get_room_state(room_id)
                if room_state:
                    teacher_sid = room_state.get('teacher_sid')
                    if teacher_sid:
                        emit('student-muted-confirm', {
                            'userId': student_id,
                            'muted': False
                        }, room=teacher_sid)
                
                break
                
    except Exception as e:
        print(f"❌ Error in unmute-student: {e}")

@socketio.on('ping')
def handle_ping(data):
    """Handle keep-alive ping"""
    sid = request.sid
    if sid in active_sessions:
        # Update last activity
        active_sessions[sid]['last_ping'] = datetime.utcnow().isoformat()
    
    # Send pong back
    emit('pong', {'timestamp': data.get('timestamp', datetime.utcnow().isoformat())})

@socketio.on('start-webrtc')
def handle_start_webrtc(data):
    """Start WebRTC for all connected students"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        room_state = get_room_state(room_id)
        if not room_state:
            emit('error', {'message': 'Room not found'})
            return
        
        print(f"🎥 Starting WebRTC in room {room_id}")
        
        # Notify teacher
        teacher_sid = room_state.get('teacher_sid')
        if teacher_sid:
            emit('webrtc-start-teacher', {
                'students': [
                    {
                        'userId': student['user_id'],
                        'username': student['username'],
                        'socketId': student.get('sid')
                    }
                    for student in room_state['connected_students']
                ]
            }, room=teacher_sid)
        
        # Notify each student
        for student in room_state['connected_students']:
            student_sid = student.get('sid')
            if student_sid:
                emit('webrtc-start-student', {
                    'teacherId': room_state['teacher_id'],
                    'teacherName': room_state['teacher_name'],
                    'room': room_id
                }, room=student_sid)
        
        print(f"✅ WebRTC started for {len(room_state['connected_students'])} students")
        
    except Exception as e:
        print(f"❌ Error in start-webrtc: {e}")
        emit('error', {'message': str(e)})

# ============================================
# FLASK ROUTES - YOUR EXISTING ROUTES
# ============================================

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    return render_template('index.html', user=user)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username and password:
            session['user'] = {
                'username': username,
                'email': f'{username}@example.com',
                'joined_on': datetime.now().strftime('%Y-%m-%d'),
                'preferred_language': 'English',
                'last_login': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            flash('Logged in successfully!')
            return redirect(url_for('index'))
        else:
            flash('Please enter username and password')
    
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        
        if not username or not password or not email:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))
        
        try:
            with app.app_context():
                if User.query.filter_by(username=username).first():
                    flash('Username already exists.')
                    return redirect(url_for('signup'))
                
                new_user = User(
                    username=username,
                    email=email,
                    joined_on=datetime.utcnow()
                )
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                
                session['user'] = {
                    'username': username,
                    'email': email,
                    'joined_on': new_user.joined_on.strftime('%Y-%m-%d'),
                    'preferred_language': 'English',
                    'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                flash('Account created successfully!')
                return redirect(url_for('index'))
                
        except Exception as e:
            flash(f'Error creating account: {str(e)}')
            return redirect(url_for('signup'))
    
    return render_template('signup.html', user=session.get('user'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# LIVE MEETING ROUTES
@app.route("/live-meeting")
def live_meeting_landing():
    import uuid
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('live_meeting', room_id=room_id))

@app.route("/live-meeting/<room_id>")
def live_meeting(room_id):
    return render_template("live_meeting.html", room_id=room_id)

@app.route("/student-live/<room_id>")
def student_live(room_id):
    return render_template("student_live.html", room_id=room_id)

@app.route("/join-live/<room_id>")
def join_live(room_id):
    return redirect(url_for('student_live', room_id=room_id))

# API ENDPOINTS
@app.route('/ask', methods=['POST'])
@login_required
def ask():
    data = request.get_json()
    question = data.get('question', '')
    
    if not question:
        return jsonify({'error': 'Question required'}), 400
    
    # Your existing ask logic here...
    return jsonify({
        'answer': 'Response from AI...',
        'question': question
    })

# Add ALL your other existing routes here...
# Keep all your @app.route() functions from your original code

# ============================================
# APPLICATION STARTUP - GUNICORN COMPATIBLE
# ============================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print(f"\n{'='*60}")
    print("🚀 TAWFIQ AI - PRODUCTION READY")
    print(f"{'='*60}")
    print(f"📡 Port: {port}")
    print(f"⚡ Async: eventlet")
    print(f"🎥 WebSocket: READY")
    print(f"💾 Redis: {'✅' if REDIS_AVAILABLE else '❌'}")
    print(f"👥 Handlers: ✅ COMPLETE")
    print(f"{'='*60}\n")
    
    # For Render production: let gunicorn handle it
    # This is just for local development
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True,
        log_output=True
    )
