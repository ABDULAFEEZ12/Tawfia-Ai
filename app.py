# app.py - COMPLETE FIXED VERSION

from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash,
    send_file, send_from_directory
)
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
from flask_sqlalchemy import SQLAlchemy
import requests
from flask_socketio import SocketIO, join_room, emit, disconnect
import eventlet
import time

# Patch standard library for eventlet
eventlet.monkey_patch()

# Load environment variables
load_dotenv()

print("🚀 Initializing Tawfiq AI Live Meeting System...")

# Initialize Flask app
app = Flask(__name__)

# Database Configuration with proper SSL for Render
DATABASE_URL = os.environ.get('DATABASE_URL')

# Fix for Render PostgreSQL - convert postgres:// to postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'your-secret-key-here')
app.config['SESSION_TYPE'] = 'redis'

# Use SQLite locally, PostgreSQL on Render with proper SSL
if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    if 'render.com' in DATABASE_URL or os.getenv('RENDER'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'connect_args': {
                'sslmode': 'require',
                'sslrootcert': 'prod-ca-2021.crt'
            },
            'pool_recycle': 300,
            'pool_pre_ping': True,
            'pool_size': 10,
            'max_overflow': 20,
            'pool_timeout': 30
        }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///local.db'

# Initialize SQLAlchemy
db = SQLAlchemy()
db.init_app(app)

# ============================================
# REDIS CONFIGURATION FOR PERSISTENT STORAGE
# ============================================

# Initialize Redis for persistent room storage
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
print(f"🔗 Redis URL: {REDIS_URL}")

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    # Test connection
    redis_client.ping()
    print("✅ Redis connected successfully")
    REDIS_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Redis connection failed: {e}")
    print("⚠️ Using in-memory storage (rooms will reset on server restart)")
    REDIS_AVAILABLE = False
    redis_client = None

# Redis helper functions
def save_to_redis(key, data, ttl=3600):
    """Save data to Redis with TTL (default 1 hour)"""
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            print(f"❌ Redis save error for key {key}: {e}")
    return False

def get_from_redis(key):
    """Get data from Redis"""
    if REDIS_AVAILABLE and redis_client:
        try:
            data = redis_client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"❌ Redis get error for key {key}: {e}")
    return None

def delete_from_redis(key):
    """Delete data from Redis"""
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.delete(key)
            return True
        except Exception as e:
            print(f"❌ Redis delete error for key {key}: {e}")
    return False

# ============================================
# SOCKET.IO CONFIGURATION WITH EVENTLET
# ============================================

# Initialize SocketIO with eventlet for WebSocket support
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='eventlet',  # CRITICAL: Must use eventlet
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1e8,
    logger=True,
    engineio_logger=True,
    transports=['websocket', 'polling']  # WebSocket first, fallback to polling
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
    """Persistent room storage in database"""
    id = db.Column(db.String(32), primary_key=True)
    room_id = db.Column(db.String(64), nullable=False, index=True)
    teacher_id = db.Column(db.String(128), nullable=False)
    teacher_name = db.Column(db.String(150))
    state = db.Column(db.String(20), default='waiting')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    room_data = db.Column(db.JSON, default={})  # Store room state as JSON

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created successfully")
        
        # Create default user if not exists
        if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            user = User.query.filter_by(username='zayd').first()
            if not user:
                user = User(username='zayd', email='zayd@example.com')
                user.set_password('secure123')
                db.session.add(user)
                db.session.commit()
                print("✅ Default user created")
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")
        print("⚠️ Continuing without database...")

# ============================================
# ROOM MANAGEMENT FUNCTIONS (PERSISTENT)
# ============================================

def save_room_state(room_id, state_data):
    """Save room state to Redis and Database"""
    room_key = f"room:{room_id}"
    
    # Save to Redis
    save_to_redis(room_key, state_data)
    
    # Save to Database
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
    """Get room state from Redis or Database"""
    room_key = f"room:{room_id}"
    
    # Try Redis first
    state = get_from_redis(room_key)
    if state:
        return state
    
    # Fallback to Database
    try:
        with app.app_context():
            room = MeetingRoom.query.filter_by(room_id=room_id).first()
            if room and room.room_data:
                # Cache in Redis for future use
                save_to_redis(room_key, room.room_data)
                return room.room_data
    except Exception as e:
        print(f"⚠️ Failed to get room from database: {e}")
    
    return None

def delete_room_state(room_id):
    """Delete room state from Redis and Database"""
    room_key = f"room:{room_id}"
    
    # Delete from Redis
    delete_from_redis(room_key)
    
    # Delete from Database
    try:
        with app.app_context():
            room = MeetingRoom.query.filter_by(room_id=room_id).first()
            if room:
                db.session.delete(room)
                db.session.commit()
    except Exception as e:
        print(f"⚠️ Failed to delete room from database: {e}")

# ============================================
# IN-MEMORY CACHE (for active sessions)
# ============================================

# These are for active connections only
active_sessions = {}  # socket_id -> user_data
room_connections = {}  # room_id -> [socket_ids]

# ============================================
# SOCKET.IO EVENT HANDLERS
# ============================================

@socketio.on('connect')
def handle_connect():
    """Handle new client connection"""
    sid = request.sid
    print(f"✅ New client connected: {sid}")
    active_sessions[sid] = {
        'connected_at': datetime.utcnow().isoformat(),
        'room': None,
        'user_type': None,
        'user_id': None
    }

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    sid = request.sid
    
    if sid in active_sessions:
        session_data = active_sessions[sid]
        room_id = session_data.get('room')
        user_id = session_data.get('user_id')
        
        print(f"❌ Client disconnected: {sid} (room: {room_id}, user: {user_id})")
        
        # Clean up from room connections
        if room_id and room_id in room_connections:
            if sid in room_connections[room_id]:
                room_connections[room_id].remove(sid)
            
            # Notify room about disconnection
            if session_data.get('user_type') == 'teacher':
                emit('teacher-disconnected', {
                    'teacherId': user_id,
                    'reason': 'Teacher left'
                }, room=room_id)
                
                # Clean up room state
                delete_room_state(room_id)
                
            elif session_data.get('user_type') == 'student':
                emit('student-disconnected', {
                    'userId': user_id,
                    'socketId': sid
                }, room=room_id)
        
        # Remove from active sessions
        del active_sessions[sid]

@socketio.on('teacher-join')
def handle_teacher_join(data):
    """Handle teacher joining a room"""
    try:
        sid = request.sid
        room_id = data.get('room')
        user_id = data.get('userId', f'teacher_{sid}')
        username = data.get('username', 'Teacher')
        
        if not room_id:
            emit('error', {'message': 'Room ID is required'})
            return
        
        print(f"👨‍🏫 Teacher {username} ({user_id}) joining room {room_id}")
        
        # Get or create room state
        room_state = get_room_state(room_id)
        if not room_state:
            room_state = {
                'state': 'waiting',
                'teacher_id': user_id,
                'teacher_name': username,
                'teacher_sid': sid,
                'created_at': datetime.utcnow().isoformat(),
                'waiting_students': [],
                'connected_students': [],
                'webrtc_started': False
            }
            print(f"📝 Created new room {room_id}")
        else:
            # Update existing room
            room_state['teacher_id'] = user_id
            room_state['teacher_name'] = username
            room_state['teacher_sid'] = sid
            room_state['updated_at'] = datetime.utcnow().isoformat()
            print(f"📝 Updated existing room {room_id}")
        
        # Save room state
        save_room_state(room_id, room_state)
        
        # Update active session
        active_sessions[sid]['room'] = room_id
        active_sessions[sid]['user_type'] = 'teacher'
        active_sessions[sid]['user_id'] = user_id
        
        # Update room connections
        if room_id not in room_connections:
            room_connections[room_id] = []
        if sid not in room_connections[room_id]:
            room_connections[room_id].append(sid)
        
        # Join Socket.IO room
        join_room(room_id)
        
        # Send room state to teacher
        emit('room-state', {
            'state': room_state['state'],
            'waitingStudents': len(room_state.get('waiting_students', [])),
            'connectedStudents': len(room_state.get('connected_students', [])),
            'teacherId': user_id,
            'teacherName': username
        })
        
        print(f"✅ Teacher successfully joined room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-join: {e}")
        emit('error', {'message': f'Failed to join room: {str(e)}'})

@socketio.on('student-join')
def handle_student_join(data):
    """Handle student joining a room"""
    try:
        sid = request.sid
        room_id = data.get('room')
        user_id = data.get('userId', f'student_{sid}')
        username = data.get('username', 'Student')
        
        if not room_id:
            emit('error', {'message': 'Room ID is required'})
            return
        
        print(f"👨‍🎓 Student {username} ({user_id}) joining room {room_id}")
        
        # Check if room exists
        room_state = get_room_state(room_id)
        if not room_state:
            emit('error', {'message': 'Room not found. Teacher may not have joined yet.'})
            print(f"❌ Room {room_id} not found for student")
            return
        
        # Update active session
        active_sessions[sid]['room'] = room_id
        active_sessions[sid]['user_type'] = 'student'
        active_sessions[sid]['user_id'] = user_id
        
        # Update room connections
        if room_id not in room_connections:
            room_connections[room_id] = []
        if sid not in room_connections[room_id]:
            room_connections[room_id].append(sid)
        
        # Join Socket.IO room
        join_room(room_id)
        
        # Add student to room state
        student_data = {
            'user_id': user_id,
            'username': username,
            'sid': sid,
            'joined_at': datetime.utcnow().isoformat(),
            'muted': False
        }
        
        if room_state['state'] == 'waiting':
            # Add to waiting students
            if user_id not in [s['user_id'] for s in room_state.get('waiting_students', [])]:
                room_state.setdefault('waiting_students', []).append(student_data)
        else:
            # Add to connected students
            if user_id not in [s['user_id'] for s in room_state.get('connected_students', [])]:
                room_state.setdefault('connected_students', []).append(student_data)
        
        # Save updated room state
        save_room_state(room_id, room_state)
        
        # Notify teacher about new student
        teacher_sid = room_state.get('teacher_sid')
        if teacher_sid:
            emit('student-joined', {
                'userId': user_id,
                'username': username,
                'socketId': sid,
                'isWaiting': room_state['state'] == 'waiting'
            }, room=teacher_sid)
        
        # Send response to student
        if room_state['state'] == 'waiting':
            emit('student-waiting-ack', {
                'status': 'waiting',
                'room': room_id,
                'teacherName': room_state.get('teacher_name', 'Teacher'),
                'message': 'Waiting for teacher to start the meeting'
            })
        else:
            emit('student-joined-ack', {
                'status': 'joined',
                'room': room_id,
                'teacherName': room_state.get('teacher_name', 'Teacher'),
                'message': 'Successfully joined the live meeting'
            })
        
        # Update room state for all
        emit('room-state', {
            'state': room_state['state'],
            'waitingStudents': len(room_state.get('waiting_students', [])),
            'connectedStudents': len(room_state.get('connected_students', [])),
            'teacherId': room_state.get('teacher_id'),
            'teacherName': room_state.get('teacher_name')
        }, room=room_id)
        
        print(f"✅ Student successfully joined room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in student-join: {e}")
        emit('error', {'message': f'Failed to join as student: {str(e)}'})

@socketio.on('start-meeting')
def handle_start_meeting(data):
    """Handle teacher starting the meeting"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID is required'})
            return
        
        print(f"🚀 Starting meeting in room {room_id}")
        
        # Get room state
        room_state = get_room_state(room_id)
        if not room_state:
            emit('error', {'message': 'Room not found'})
            return
        
        # Update room state
        room_state['state'] = 'live'
        room_state['started_at'] = datetime.utcnow().isoformat()
        
        # Move waiting students to connected
        if 'waiting_students' in room_state:
            for student in room_state['waiting_students']:
                if student['user_id'] not in [s['user_id'] for s in room_state.get('connected_students', [])]:
                    room_state.setdefault('connected_students', []).append(student)
            room_state['waiting_students'] = []
        
        # Save updated room state
        save_room_state(room_id, room_state)
        
        # Notify all participants
        emit('room-started', {
            'room': room_id,
            'teacherId': room_state.get('teacher_id'),
            'teacherName': room_state.get('teacher_name'),
            'startedAt': room_state['started_at']
        }, room=room_id)
        
        # Send individual notifications to students
        for student in room_state.get('connected_students', []):
            student_sid = student.get('sid')
            if student_sid:
                emit('meeting-started', {
                    'room': room_id,
                    'teacherName': room_state.get('teacher_name'),
                    'message': 'The meeting has started!'
                }, room=student_sid)
        
        # Update room state for all
        emit('room-state', {
            'state': 'live',
            'waitingStudents': 0,
            'connectedStudents': len(room_state.get('connected_students', [])),
            'teacherId': room_state.get('teacher_id'),
            'teacherName': room_state.get('teacher_name')
        }, room=room_id)
        
        print(f"✅ Meeting started in room {room_id} with {len(room_state.get('connected_students', []))} students")
        
    except Exception as e:
        print(f"❌ Error in start-meeting: {e}")
        emit('error', {'message': f'Failed to start meeting: {str(e)}'})

@socketio.on('end-meeting')
def handle_end_meeting(data):
    """Handle teacher ending the meeting"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID is required'})
            return
        
        print(f"🛑 Ending meeting in room {room_id}")
        
        # Get room state
        room_state = get_room_state(room_id)
        if room_state:
            # Notify all participants
            emit('room-ended', {
                'room': room_id,
                'teacherId': room_state.get('teacher_id'),
                'teacherName': room_state.get('teacher_name'),
                'message': 'Meeting has ended',
                'endedAt': datetime.utcnow().isoformat()
            }, room=room_id)
        
        # Clean up room state
        delete_room_state(room_id)
        
        # Clean up connections
        if room_id in room_connections:
            for sid in room_connections[room_id]:
                if sid in active_sessions:
                    active_sessions[sid]['room'] = None
            del room_connections[room_id]
        
        print(f"✅ Meeting ended and cleaned up for room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in end-meeting: {e}")
        emit('error', {'message': f'Failed to end meeting: {str(e)}'})

@socketio.on('webrtc-signal')
def handle_webrtc_signal(data):
    """Handle WebRTC signaling"""
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
# FLASK ROUTES (Keep your existing routes below)
# ============================================

# [All your existing Flask routes remain exactly the same]
# I'm not including them here to save space, but keep everything from:
# @app.route('/') through to the end of your file

# ============================================
# GUNICORN CONFIGURATION (render.yaml or Procfile)
# ============================================

"""
Add this to your Procfile:
web: gunicorn --worker-class eventlet --workers 2 --threads 4 --timeout 300 --bind 0.0.0.0:$PORT app:app

Or create a gunicorn_config.py:
bind = "0.0.0.0:5000"
workers = 2
worker_class = "eventlet"
threads = 4
timeout = 300
keepalive = 10
max_requests = 1000
max_requests_jitter = 50
"""

# ============================================
# APPLICATION STARTUP
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print(f"\n{'='*60}")
    print("🚀 TAWFIQ AI LIVE MEETING SYSTEM - COMPLETE FIX")
    print(f"{'='*60}")
    print(f"📡 Server URL: http://localhost:{port}")
    print(f"⚡ Async Mode: eventlet (WebSocket ready)")
    print(f"💾 Redis: {'✅ Connected' if REDIS_AVAILABLE else '❌ Not available'}")
    print(f"🗄️  Database: {'✅ PostgreSQL' if DATABASE_URL and 'postgresql' in DATABASE_URL else '✅ SQLite'}")
    print(f"🎥 Live Meeting: ✅ READY (with persistent storage)")
    print(f"👥 Active Sessions: {len(active_sessions)}")
    print(f"🏠 Active Rooms: {len(room_connections)}")
    print(f"{'='*60}\n")
    
    # Start the server
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug,
        log_output=True,
        use_reloader=True
    )
