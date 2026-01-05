import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
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
from flask_socketio import SocketIO, join_room, emit, leave_room
import ssl
from uuid import uuid4
import uuid

# Load environment variables
load_dotenv()

print("✅ API KEY:", os.getenv("GOOGLE_NEWS_API_KEY"))
print("✅ CX:", os.getenv("GOOGLE_CX"))

# Initialize Flask app
app = Flask(__name__)

# Database Configuration with proper SSL for Render
DATABASE_URL = os.environ.get('DATABASE_URL')

# Fix for Render PostgreSQL - convert postgres:// to postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Allow cookies in fetch
app.config['SESSION_COOKIE_SECURE'] = False    # Only True if HTTPS
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'your-secret-key-here')

# Use SQLite locally, PostgreSQL on Render with proper SSL
if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    # Render requires SSL for PostgreSQL
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
    # Fallback to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///local.db'

# Initialize SQLAlchemy
db = SQLAlchemy()
db.init_app(app)

# Initialize SocketIO with proper settings
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)

# --- Models ---
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

class Room(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    teacher_id = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created successfully")
        
        # Create default user if not exists (only for SQLite)
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

# --- Get Questions for User with error handling ---
def get_questions_for_user(username):
    try:
        with app.app_context():
            questions = UserQuestions.query \
                .filter(func.lower(UserQuestions.username) == username.lower()) \
                .order_by(UserQuestions.timestamp.desc()) \
                .limit(10) \
                .all()
            return [
                {
                    "question": q.question,
                    "answer": q.answer,
                    "timestamp": q.timestamp.strftime("%Y-%m-%d %H:%M:%S") if q.timestamp else "Unknown"
                }
                for q in questions
            ]
    except Exception as e:
        print(f"⚠️ Database error in get_questions_for_user: {e}")
        return []

# --- Save a Question and Answer for a User ---
def save_question_and_answer(username, question, answer):
    try:
        with app.app_context():
            existing_entry = UserQuestions.query.filter_by(username=username, question=question).first()

            if existing_entry:
                existing_entry.answer = answer
                existing_entry.timestamp = datetime.utcnow()
                print(f"🔁 Updated existing Q&A for '{username}'")
            else:
                new_entry = UserQuestions(
                    username=username,
                    question=question,
                    answer=answer,
                    timestamp=datetime.utcnow()
                )
                db.session.add(new_entry)
                print(f"✅ Saved new Q&A for '{username}'")

            db.session.commit()

    except Exception as e:
        print(f"❌ Failed to save Q&A for '{username}': {e}")
        try:
            db.session.rollback()
        except:
            pass

# --- Redis Cache Setup ---
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))
redis_db = int(os.getenv("REDIS_DB", 0))
redis_password = os.getenv("REDIS_PASSWORD", None)

try:
    r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
    r.ping()
    print("✅ Redis connected successfully")
except:
    print("⚠️ Redis not available, using in-memory cache")
    r = None

# --- File-Based Cache ---
CACHE_FILE = "tawfiq_cache.json"

if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            question_cache = json.load(f)
    except json.JSONDecodeError:
        question_cache = {}
else:
    question_cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(question_cache, f, indent=2, ensure_ascii=False)

# --- Load JSON datasets ---
def load_json_data(file_name, data_variable_name):
    data = {}
    file_path = os.path.join(os.path.dirname(__file__), 'DATA', file_name)
    print(f"Attempting to load {data_variable_name} data from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ Successfully loaded {data_variable_name} data")
    except FileNotFoundError:
        print(f"❌ ERROR: {data_variable_name} data file not found at {file_path}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON Decode Error in {file_path}: {e}")
        if 'daily_duas' in file_name:
            data = {"duas": []}
    except Exception as e:
        print(f"❌ Unexpected error while loading {file_name}: {e}")
    return data

# Load datasets
hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Islamic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')
daily_duas = load_json_data('daily_duas.json', 'Daily Duas')
islamic_motivation = load_json_data('islamic_motivation.json', 'Islamic Motivation')

# --- OpenRouter API Key ---
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
if not openrouter_api_key:
    print("⚠️ OPENROUTER_API_KEY environment variable not set.")

# ============================================
# Live Meeting In-Memory Storage
# ============================================
rooms = {}           # room_id -> room data
participants = {}    # socket_id -> participant info
room_authority = {}  # room_id -> authority state

# ============================================
# Helper Functions for Live Meeting
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'participants': {},      # socket_id -> {'username', 'role', 'joined_at'}
            'teacher_sid': None,
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def get_room_authority(room_id):
    """Get or create authority state for a room"""
    if room_id not in room_authority:
        room_authority[room_id] = {
            'muted_all': False,
            'cameras_disabled': False,
            'mic_requests': {},
            'questions_enabled': True,
            'question_visibility': 'public'
        }
    return room_authority[room_id]

def get_participants_list(room_id, exclude_sid=None):
    """Get list of all participants in room except exclude_sid"""
    if room_id not in rooms:
        return []
    
    room = rooms[room_id]
    result = []
    
    for sid, info in room['participants'].items():
        if sid != exclude_sid:
            result.append({
                'sid': sid,
                'username': info['username'],
                'role': info['role']
            })
    
    return result

def cleanup_room(room_id):
    """Remove empty rooms"""
    if room_id in rooms:
        room = rooms[room_id]
        if not room['participants']:
            del rooms[room_id]
            if room_id in room_authority:
                del room_authority[room_id]
            with app.app_context():
                Room.query.filter_by(id=room_id).delete()
                db.session.commit()

# ============================================
# Socket.IO Event Handlers - LIVE MEETING
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    # CRITICAL FIX: Join client to their private SID room for direct messaging
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    print(f"✅ Client connected: {sid} (joined private room: {sid})")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    
    # Find which room this participant is in
    participant = participants.get(sid)
    if not participant:
        return
    
    room_id = participant['room_id']
    
    if room_id in rooms:
        room = rooms[room_id]
        
        # Notify all other participants
        if sid in room['participants']:
            participant_info = room['participants'][sid]
            
            # Remove from room
            del room['participants'][sid]
            
            # Update teacher_sid if teacher left
            if sid == room['teacher_sid']:
                room['teacher_sid'] = None
                # Notify students that teacher left
                for participant_sid in room['participants']:
                    if room['participants'][participant_sid]['role'] == 'student':
                        emit('teacher-disconnected', room=participant_sid)
            
            # Notify others
            emit('participant-left', {
                'sid': sid,
                'username': participant_info['username'],
                'role': participant_info['role']
            }, room=room_id, skip_sid=sid)
            
            print(f"❌ {participant_info['username']} left room {room_id}")
        
        # Clean up empty room
        cleanup_room(room_id)
    
    # Remove from participants
    if sid in participants:
        del participants[sid]

@socketio.on('join-room')
def handle_join_room(data):
    """Join room and get all existing participants - FIXED"""
    try:
        sid = request.sid
        room_id = data.get('room')
        role = data.get('role', 'student')
        username = data.get('username', 'Teacher' if role == 'teacher' else f'Student_{sid[:6]}')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👤 {username} ({role}) joining room: {room_id}")
        
        room = get_or_create_room(room_id)
        authority_state = get_room_authority(room_id)
        
        # Check if teacher already exists
        if role == 'teacher' and room['teacher_sid']:
            emit('error', {'message': 'Room already has a teacher'})
            return
        
        # Add to room
        room['participants'][sid] = {
            'username': username,
            'role': role,
            'joined_at': datetime.utcnow().isoformat()
        }
        
        # Update teacher reference
        if role == 'teacher':
            room['teacher_sid'] = sid
            authority_state['teacher_sid'] = sid
            
            with app.app_context():
                existing_room = Room.query.get(room_id)
                if not existing_room:
                    room_db = Room(
                        id=room_id,
                        teacher_id=sid,
                        teacher_name=username,
                        is_active=True
                    )
                    db.session.add(room_db)
                else:
                    existing_room.teacher_id = sid
                    existing_room.teacher_name = username
                db.session.commit()
            
            # Notify all students that teacher joined
            for participant_sid in room['participants']:
                if room['participants'][participant_sid]['role'] == 'student':
                    emit('teacher-joined', {
                        'teacher_sid': sid,
                        'teacher_name': username
                    }, room=participant_sid)
        
        # Update participant info
        participants[sid] = {
            'room_id': room_id,
            'username': username,
            'role': role
        }
        
        # Join the socket room
        join_room(room_id)
        
        # Get all existing participants (excluding self)
        existing_participants = get_participants_list(room_id, exclude_sid=sid)
        
        # Send room joined confirmation
        emit('room-joined', {
            'room': room_id,
            'sid': sid,
            'username': username,
            'role': role,
            'existing_participants': existing_participants,
            'teacher_sid': room['teacher_sid'],
            'is_waiting': (role == 'student' and not room['teacher_sid'])  # Inform student they're waiting
        })
        
        # Notify all other participants about new joiner
        emit('new-participant', {
            'sid': sid,
            'username': username,
            'role': role
        }, room=room_id, skip_sid=sid)
        
        # Send authority state if student and teacher exists
        if role == 'student' and room['teacher_sid']:
            emit('room-state', {
                'muted_all': authority_state['muted_all'],
                'cameras_disabled': authority_state['cameras_disabled'],
                'questions_enabled': authority_state['questions_enabled'],
                'question_visibility': authority_state['question_visibility']
            })
        
        # Log room status
        print(f"✅ {username} joined room {room_id}. Total participants: {len(room['participants'])}")
        
    except Exception as e:
        print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# ============================================
# WebRTC Signaling - Full Mesh Support - FIXED
# ============================================
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        offer = data.get('offer')
        
        if not all([room_id, target_sid, offer]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        print(f"📨 {request.sid[:8]} → offer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room (requires client to join their SID room on connect)
        emit('webrtc-offer', {
            'from_sid': request.sid,
            'offer': offer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error relaying offer: {e}")

@socketio.on('webrtc-answer')
def handle_webrtc_answer(data):
    """Relay WebRTC answer to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        answer = data.get('answer')
        
        if not all([room_id, target_sid, answer]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        print(f"📨 {request.sid[:8]} → answer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-answer', {
            'from_sid': request.sid,
            'answer': answer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error relaying answer: {e}")

@socketio.on('webrtc-ice-candidate')
def handle_webrtc_ice_candidate(data):
    """Relay ICE candidate to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        candidate = data.get('candidate')
        
        if not all([room_id, target_sid, candidate]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        print(f"📨 {request.sid[:8]} → ICE → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error relaying ICE candidate: {e}")

# ============================================
# NEW: Full Mesh Initiation System
# ============================================
@socketio.on('request-full-mesh')
def handle_request_full_mesh(data):
    """Initiate full mesh connections between all participants"""
    try:
        room_id = data.get('room')
        sid = request.sid
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        
        # Verify participant is in room
        if sid not in room['participants']:
            return
        
        # Get all other participants in room
        other_participants = []
        for other_sid, info in room['participants'].items():
            if other_sid != sid:
                other_participants.append({
                    'sid': other_sid,
                    'username': info['username'],
                    'role': info['role']
                })
        
        # Send list of peers to connect to
        emit('initiate-mesh-connections', {
            'peers': other_participants,
            'room': room_id
        }, room=sid)
        
        print(f"🔗 Initiating full mesh for {sid[:8]} with {len(other_participants)} peers")
        
    except Exception as e:
        print(f"❌ Error in request-full-mesh: {e}")

# ============================================
# Teacher Authority System
# ============================================
@socketio.on('teacher-mute-all')
def handle_teacher_mute_all(data):
    """Teacher mutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['muted_all'] = True
        
        # Notify all students
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('room-muted', {'muted': True}, room=sid)
        
        print(f"🔇 Teacher muted all in room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-mute-all: {e}")

@socketio.on('teacher-unmute-all')
def handle_teacher_unmute_all(data):
    """Teacher unmutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['muted_all'] = False
        
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('room-muted', {'muted': False}, room=sid)
        
        print(f"🔊 Teacher unmuted all in room {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-unmute-all: {e}")

# ============================================
# Control Events - FIXED
# ============================================
@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students - FIXED"""
    try:
        room_id = data.get('room')
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can start broadcast'})
            return
        
        print(f"📢 Teacher starting broadcast in room: {room_id}")
        
        # Get all student SIDs
        student_sids = []
        student_info = []
        for sid, info in room['participants'].items():
            if info['role'] == 'student':
                student_sids.append(sid)
                student_info.append({
                    'sid': sid,
                    'username': info['username']
                })
        
        # Notify teacher
        emit('broadcast-ready', {
            'student_sids': student_sids,
            'student_info': student_info,
            'student_count': len(student_sids),
            'room': room_id
        }, room=teacher_sid)
        
        # FIX: Initiate WebRTC connections for each student
        for student_sid in student_sids:
            # Send list of all peers to connect to (full mesh)
            peers_to_connect = []
            for other_sid in room['participants']:
                if other_sid != student_sid:  # Don't connect to self
                    peers_to_connect.append({
                        'sid': other_sid,
                        'username': room['participants'][other_sid]['username'],
                        'role': room['participants'][other_sid]['role']
                    })
            
            emit('initiate-full-mesh', {
                'peers': peers_to_connect,
                'teacher_sid': teacher_sid,
                'room': room_id
            }, room=student_sid)
        
    except Exception as e:
        print(f"❌ Error in start-broadcast: {e}")
        emit('error', {'message': str(e)})

@socketio.on('ping')
def handle_ping(data):
    """Keep-alive ping"""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

# ============================================
# Flask Routes
# ============================================
@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    username = user['username']
    questions = get_questions_for_user(username)
    
    if questions is None:
        questions = []

    return render_template('index.html', user=user, questions=questions)

@app.route('/test-db')
def test_db():
    try:
        with app.app_context():
            db.session.execute("SELECT 1")
            return "✅ Database connection successful"
    except Exception as e:
        return f"❌ Database connection failed: {e}"

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

                if User.query.filter_by(email=email).first():
                    flash('Email already registered.')
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json()
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
        else:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()

        try:
            with app.app_context():
                user = User.query.filter_by(username=username).first()
                
                # If no user found in database or database error, use temporary login
                if not user:
                    # Temporary login for testing
                    if username and password:
                        session['user'] = {
                            'username': username,
                            'email': f'{username}@example.com',
                            'joined_on': datetime.now().strftime('%Y-%m-%d'),
                            'preferred_language': 'English',
                            'last_login': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        }
                        
                        if request.is_json:
                            return jsonify({'success': True, 'message': 'Login successful (temporary mode)', 'user': session['user']})
                        else:
                            flash('Logged in successfully (temporary mode)!')
                            return redirect(url_for('index'))
                    else:
                        if request.is_json:
                            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
                        else:
                            flash('Please enter username and password.')
                            return redirect(url_for('login'))
                
                # Normal database login
                if user.check_password(password):
                    user.last_login = datetime.utcnow()
                    db.session.commit()

                    session.permanent = True
                    session['user'] = {
                        'username': user.username,
                        'email': user.email,
                        'joined_on': user.joined_on.strftime('%Y-%m-%d'),
                        'preferred_language': 'English',
                        'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S')
                    }

                    if request.is_json:
                        return jsonify({'success': True, 'message': 'Login successful', 'user': session['user']})
                    else:
                        flash('Logged in successfully!')
                        return redirect(url_for('index'))

                else:
                    if request.is_json:
                        return jsonify({'success': False, 'error': 'Invalid username or password'}), 401
                    else:
                        flash('Invalid username or password.')
                        return redirect(url_for('login'))

        except Exception as e:
            print(f"Database error: {e}")
            # Fallback to temporary login on database error
            if username and password:
                session['user'] = {
                    'username': username,
                    'email': f'{username}@example.com',
                    'joined_on': datetime.now().strftime('%Y-%m-%d'),
                    'preferred_language': 'English',
                    'last_login': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                if request.is_json:
                    return jsonify({'success': True, 'message': 'Login successful (fallback mode)', 'user': session['user']})
                else:
                    flash('Logged in successfully (database temporarily unavailable)!')
                    return redirect(url_for('index'))
            else:
                if request.is_json:
                    return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500
                else:
                    flash(f'Database error: {str(e)}')
                    return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return """
    <h2>You have been logged out</h2>
    <a href="/login">Login Again</a> | <a href="/signup">Create Account</a>
    """

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    return render_template('forgot_password.html')

# --- Trivia Levels ---
levels = {
    1: [
        {
            "question": "Which Surah is called 'The Opening' of the Quran?",
            "options": ["Al-Baqarah", "Al-Fatiha", "Al-Ikhlas", "Yasin"],
            "answer": "Al-Fatiha"
        },
        {
            "question": "How many days is Ramadan observed each year?",
            "options": ["28", "29 or 30", "31", "40"],
            "answer": "29 or 30"
        },
        {
            "question": "What is the Arabic word for God?",
            "options": ["Rabb", "Ilah", "Allah", "Khaliq"],
            "answer": "Allah"
        },
        {
            "question": "Which direction do Muslims face during prayer?",
            "options": ["East", "Qibla (Kaaba)", "North", "Jerusalem"],
            "answer": "Qibla (Kaaba)"
        },
        {
            "question": "Who was the first wife of Prophet Muhammad (PBUH)?",
            "options": ["Aisha", "Sawdah", "Khadijah", "Hafsa"],
            "answer": "Khadijah"
        }
    ],

    2: [
        {
            "question": "What is the name of the Islamic month of fasting?",
            "options": ["Shawwal", "Ramadan", "Muharram", "Dhul-Hijjah"],
            "answer": "Ramadan"
        },
        {
            "question": "Which prophet is known as the 'Father of Arabs'?",
            "options": ["Ismail", "Ibrahim", "Ishaq", "Yaqub"],
            "answer": "Ismail"
        },
        {
            "question": "How many times is the name 'Muhammad' mentioned in the Quran?",
            "options": ["4", "5", "6", "7"],
            "answer": "4"
        },
        {
            "question": "What is the term for the Islamic declaration of faith?",
            "options": ["Takbir", "Shahada", "Tahlil", "Tasbih"],
            "answer": "Shahada"
        },
        {
            "question": "Which angel will blow the trumpet on Judgment Day?",
            "options": ["Jibril", "Mikail", "Israfil", "Malik"],
            "answer": "Israfil"
        }
    ],

    3: [
        {
            "question": "What is the name of the well in Mecca that appeared for Hajar and Ismail?",
            "options": ["Zamzam", "Ayn Zubaydah", "Bir Ali", "Qanatir"],
            "answer": "Zamzam"
        },
        {
            "question": "Which Surah is known as 'The Heart of the Quran'?",
            "options": ["Yasin", "Al-Fatiha", "Al-Baqarah", "Al-Ikhlas"],
            "answer": "Yasin"
        },
        {
            "question": "How many Rak'ahs are in Maghrib prayer?",
            "options": ["2", "3", "4", "5"],
            "answer": "3"
        },
        {
            "question": "What is the term for the Islamic pilgrimage to Mecca?",
            "options": ["Umrah", "Hajj", "Tawaf", "Sa'i"],
            "answer": "Hajj"
        },
        {
            "question": "Which prophet is known for building the Ark?",
            "options": ["Nuh", "Musa", "Yusuf", "Ibrahim"],
            "answer": "Nuh"
        }
    ],

    4: [
        {
            "question": "What is the name of the black stone in the Kaaba?",
            "options": ["Maqam Ibrahim", "Hajar al-Aswad", "Rukn Yamani", "Hijr Ismail"],
            "answer": "Hajar al-Aswad"
        },
        {
            "question": "Which Surah begins with 'Alif Lam Meem'?",
            "options": ["Al-Baqarah", "Al-Imran", "Al-Fatiha", "Yasin"],
            "answer": "Al-Baqarah"
        },
        {
            "question": "What is the term for the Islamic charity given at Eid?",
            "options": ["Zakat al-Mal", "Zakat al-Fitr", "Sadaqah", "Kaffarah"],
            "answer": "Zakat al-Fitr"
        },
        {
            "question": "Which prophet is called 'Kalimullah' (Speaker with Allah)?",
            "options": ["Musa", "Ibrahim", "Isa", "Dawud"],
            "answer": "Musa"
        },
        {
            "question": "How many Surahs are in the 30th Juz of the Quran?",
            "options": ["34", "36", "37", "38"],
            "answer": "37"
        }
    ],

    5: [
        {
            "question": "What is the name of the Prophet's night journey from Mecca to Jerusalem?",
            "options": ["Hijrah", "Isra", "Miraj", "Ghazwa"],
            "answer": "Isra"
        },
        {
            "question": "Which Surah is called 'The Sovereignty'?",
            "options": ["Al-Mulk", "Al-Waqi'ah", "Al-Qalam", "Al-Hadid"],
            "answer": "Al-Mulk"
        },
        {
            "question": "What is the term for the Islamic pre-dawn meal in Ramadan?",
            "options": ["Iftar", "Suhoor", "Taraweeh", "Qiyam"],
            "answer": "Suhoor"
        },
        {
            "question": "Which companion was known as 'The Lion of Allah'?",
            "options": ["Umar", "Ali", "Hamza", "Khalid"],
            "answer": "Hamza"
        },
        {
            "question": "How many times is 'Bismillah' repeated in the Quran?",
            "options": ["112", "113", "114", "115"],
            "answer": "114"
        }
    ],

    6: [
        {
            "question": "Which prophet is known for his patience in the face of illness?",
            "options": ["Ayyub", "Yunus", "Yusuf", "Ibrahim"],
            "answer": "Ayyub"
        },
        {
            "question": "What is the name of the Islamic prayer performed at night in Ramadan?",
            "options": ["Tahajjud", "Taraweeh", "Witr", "Qiyam"],
            "answer": "Taraweeh"
        },
        {
            "question": "Which Surah is known as 'The Cow'?",
            "options": ["Al-Baqarah", "Al-Imran", "An-Nisa", "Al-Ma'idah"],
            "answer": "Al-Baqarah"
        },
        {
            "question": "What is the term for the Islamic funeral prayer?",
            "options": ["Janazah", "Taraweeh", "Tahajjud", "Witr"],
            "answer": "Janazah"
        },
        {
            "question": "Which city was the first capital of Islam?",
            "options": ["Mecca", "Medina", "Kufa", "Damascus"],
            "answer": "Medina"
        }
    ],

    7: [
        {
            "question": "What is the name of the Islamic festival marking the end of Ramadan?",
            "options": ["Eid al-Adha", "Eid al-Fitr", "Mawlid", "Laylat al-Qadr"],
            "answer": "Eid al-Fitr"
        },
        {
            "question": "Which prophet is known for his beautiful voice and the Psalms?",
            "options": ["Dawud", "Sulaiman", "Musa", "Yusuf"],
            "answer": "Dawud"
        },
        {
            "question": "What is the term for the Islamic ruling on permissible and forbidden?",
            "options": ["Halal & Haram", "Sunnah & Bid'ah", "Fard & Mustahabb", "Makruh & Mubah"],
            "answer": "Halal & Haram"
        },
        {
            "question": "Which Surah is known as 'The Purity'?",
            "options": ["Al-Ikhlas", "Al-Falaq", "An-Nas", "Al-Kafirun"],
            "answer": "Al-Ikhlas"
        },
        {
            "question": "Who was the first male to accept Islam?",
            "options": ["Abu Bakr", "Ali", "Zayd", "Umar"],
            "answer": "Abu Bakr"
        }
    ],

    8: [
        {
            "question": "What is the name of the Islamic festival of sacrifice?",
            "options": ["Eid al-Fitr", "Eid al-Adha", "Mawlid", "Laylat al-Qadr"],
            "answer": "Eid al-Adha"
        },
        {
            "question": "Which prophet is known for his wisdom and the story of the two women?",
            "options": ["Sulaiman", "Dawud", "Yusuf", "Ibrahim"],
            "answer": "Sulaiman"
        },
        {
            "question": "What is the term for the Islamic concept of divine decree?",
            "options": ["Qadr", "Tawakkul", "Tawhid", "Akhirah"],
            "answer": "Qadr"
        },
        {
            "question": "Which Surah is known as 'The Light'?",
            "options": ["An-Nur", "Al-Hadid", "Al-Mumtahanah", "Al-Ahzab"],
            "answer": "An-Nur"
        },
        {
            "question": "Who was the Prophet's foster mother?",
            "options": ["Halimah", "Amina", "Khadijah", "Sumayyah"],
            "answer": "Halimah"
        }
    ],

    9: [
        {
            "question": "What is the name of the Islamic prayer performed at dawn?",
            "options": ["Fajr", "Dhuhr", "Asr", "Maghrib"],
            "answer": "Fajr"
        },
        {
            "question": "Which prophet is known for interpreting dreams?",
            "options": ["Yusuf", "Sulaiman", "Ibrahim", "Dawud"],
            "answer": "Yusuf"
        },
        {
            "question": "What is the term for the Islamic call to prayer?",
            "options": ["Iqamah", "Adhan", "Takbir", "Tahlil"],
            "answer": "Adhan"
        },
        {
            "question": "Which Surah is known as 'The Dawn'?",
            "options": ["Al-Falaq", "An-Nas", "Al-Ikhlas", "Al-Kafirun"],
            "answer": "Al-Falaq"
        },
        {
            "question": "Who was the first martyr in Islam?",
            "options": ["Sumayyah", "Bilal", "Hamza", "Umar"],
            "answer": "Sumayyah"
        }
    ],

    10: [
        {
            "question": "What is the name of the Islamic prayer performed at midday?",
            "options": ["Dhuhr", "Asr", "Fajr", "Isha"],
            "answer": "Dhuhr"
        },
        {
            "question": "Which prophet is known for his patience and the story of the whale?",
            "options": ["Yunus", "Musa", "Yusuf", "Ayyub"],
            "answer": "Yunus"
        },
        {
            "question": "What is the term for the Islamic fast-breaking meal?",
            "options": ["Suhoor", "Iftar", "Taraweeh", "Qiyam"],
            "answer": "Iftar"
        },
        {
            "question": "Which Surah is known as 'The People'?",
            "options": ["An-Nas", "Al-Falaq", "Al-Ikhlas", "Al-Kafirun"],
            "answer": "An-Nas"
        },
        {
            "question": "Who was the first caliph after Prophet Muhammad?",
            "options": ["Umar", "Abu Bakr", "Ali", "Uthman"],
            "answer": "Abu Bakr"
        }
    ],

    11: [
        {
            "question": "What is the name of the Islamic prayer performed in the late afternoon?",
            "options": ["Asr", "Dhuhr", "Maghrib", "Isha"],
            "answer": "Asr"
        },
        {
            "question": "Which prophet is known for his staff and parting the sea?",
            "options": ["Musa", "Yusuf", "Nuh", "Yunus"],
            "answer": "Musa"
        },
        {
            "question": "What is the term for the Islamic tax on wealth?",
            "options": ["Sadaqah", "Zakat", "Kaffarah", "Fitrah"],
            "answer": "Zakat"
        },
        {
            "question": "Which Surah is known as 'The Iron'?",
            "options": ["Al-Hadid", "Al-Waqi'ah", "Al-Qalam", "Al-Mulk"],
            "answer": "Al-Hadid"
        },
        {
            "question": "Who was the first female scholar of Islam?",
            "options": ["Aisha", "Khadijah", "Fatimah", "Hafsa"],
            "answer": "Aisha"
        }
    ],

    12: [
        {
            "question": "What is the name of the Islamic prayer performed after sunset?",
            "options": ["Maghrib", "Isha", "Fajr", "Dhuhr"],
            "answer": "Maghrib"
        },
        {
            "question": "Which prophet is known for his kingdom and the hoopoe bird?",
            "options": ["Sulaiman", "Dawud", "Yusuf", "Ibrahim"],
            "answer": "Sulaiman"
        },
        {
            "question": "What is the term for the Islamic concept of gratitude?",
            "options": ["Shukr", "Sabr", "Tawakkul", "Ihsan"],
            "answer": "Shukr"
        },
        {
            "question": "Which Surah is known as 'The Inevitable'?",
            "options": ["Al-Waqi'ah", "Al-Qiyamah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Waqi'ah"
        },
        {
            "question": "Who was the first person to compile the Quran into a book?",
            "options": ["Abu Bakr", "Umar", "Uthman", "Ali"],
            "answer": "Abu Bakr"
        }
    ],

    13: [
        {
            "question": "What is the name of the Islamic prayer performed at night?",
            "options": ["Isha", "Tahajjud", "Taraweeh", "Witr"],
            "answer": "Isha"
        },
        {
            "question": "Which prophet is known for his cloak and the two gardens?",
            "options": ["Yusuf", "Sulaiman", "Dawud", "Ibrahim"],
            "answer": "Yusuf"
        },
        {
            "question": "What is the term for the Islamic concept of reliance on Allah?",
            "options": ["Tawakkul", "Sabr", "Shukr", "Ihsan"],
            "answer": "Tawakkul"
        },
        {
            "question": "Which Surah is known as 'The Resurrection'?",
            "options": ["Al-Qiyamah", "Al-Waqi'ah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Qiyamah"
        },
        {
            "question": "Who was the first person to memorize the entire Quran?",
            "options": ["Hafsa", "Aisha", "Uthman", "Ali"],
            "answer": "Hafsa"
        }
    ],

    14: [
        {
            "question": "What is the name of the Islamic prayer performed during funerals?",
            "options": ["Janazah", "Taraweeh", "Tahajjud", "Witr"],
            "answer": "Janazah"
        },
        {
            "question": "Which prophet is known for his ring and control over jinn?",
            "options": ["Sulaiman", "Dawud", "Yusuf", "Ibrahim"],
            "answer": "Sulaiman"
        },
        {
            "question": "What is the term for the Islamic concept of excellence in worship?",
            "options": ["Ihsan", "Iman", "Islam", "Taqwa"],
            "answer": "Ihsan"
        },
        {
            "question": "Which Surah is known as 'The Event'?",
            "options": ["Al-Waqi'ah", "Al-Qiyamah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Waqi'ah"
        },
        {
            "question": "Who was the first person to lead prayers in the Prophet's absence?",
            "options": ["Abu Bakr", "Umar", "Ali", "Bilal"],
            "answer": "Abu Bakr"
        }
    ],

    15: [
        {
            "question": "What is the name of the Islamic prayer performed during Eid?",
            "options": ["Eid Salah", "Taraweeh", "Janazah", "Witr"],
            "answer": "Eid Salah"
        },
        {
            "question": "Which prophet is known for his patience and the story of the cow?",
            "options": ["Musa", "Yusuf", "Ibrahim", "Nuh"],
            "answer": "Musa"
        },
        {
            "question": "What is the term for the Islamic concept of spiritual excellence?",
            "options": ["Taqwa", "Ihsan", "Iman", "Tawhid"],
            "answer": "Ihsan"
        },
        {
            "question": "Which Surah is known as 'The Overwhelming'?",
            "options": ["Al-Ghashiyah", "Al-Waqi'ah", "Al-Qiyamah", "Al-Mulk"],
            "answer": "Al-Ghashiyah"
        },
        {
            "question": "Who was the first person to compile Hadith into a book?",
            "options": ["Imam Bukhari", "Imam Muslim", "Imam Malik", "Imam Ahmad"],
            "answer": "Imam Malik"
        }
    ],

    16: [
        {
            "question": "What is the name of the Islamic prayer performed during Hajj at Arafat?",
            "options": ["Wuquf", "Tawaf", "Sa'i", "Ramy"],
            "answer": "Wuquf"
        },
        {
            "question": "Which prophet is known for his dream of stars and the moon?",
            "options": ["Yusuf", "Ibrahim", "Yaqub", "Ismail"],
            "answer": "Yusuf"
        },
        {
            "question": "What is the term for the Islamic concept of divine unity?",
            "options": ["Tawhid", "Shirk", "Qadr", "Iman"],
            "answer": "Tawhid"
        },
        {
            "question": "Which Surah is known as 'The Pen'?",
            "options": ["Al-Qalam", "Al-Waqi'ah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Qalam"
        },
        {
            "question": "Who was the first person to translate the Quran into another language?",
            "options": ["Salman al-Farsi", "Umar", "Ali", "Abu Bakr"],
            "answer": "Salman al-Farsi"
        }
    ],

    17: [
        {
            "question": "What is the name of the Islamic prayer performed during Laylat al-Qadr?",
            "options": ["Qiyam", "Taraweeh", "Tahajjud", "Witr"],
            "answer": "Qiyam"
        },
        {
            "question": "Which prophet is known for his golden calf story?",
            "options": ["Musa", "Harun", "Yusuf", "Ibrahim"],
            "answer": "Musa"
        },
        {
            "question": "What is the term for the Islamic concept of striving in Allah's path?",
            "options": ["Jihad", "Hijrah", "Dawah", "Ihsan"],
            "answer": "Jihad"
        },
        {
            "question": "Which Surah is known as 'The Cloaked One'?",
            "options": ["Al-Muddathir", "Al-Muzzammil", "Al-Qalam", "Al-Hadid"],
            "answer": "Al-Muddathir"
        },
        {
            "question": "Who was the first caliph to be assassinated?",
            "options": ["Umar", "Uthman", "Ali", "Abu Bakr"],
            "answer": "Umar"
        }
    ],

    18: [
        {
            "question": "What is the name of the Islamic prayer performed during the eclipse?",
            "options": ["Salat al-Kusuf", "Salat al-Istisqa", "Salat al-Taraweeh", "Salat al-Janazah"],
            "answer": "Salat al-Kusuf"
        },
        {
            "question": "Which prophet is known for his miraculous birth without a father?",
            "options": ["Isa", "Yahya", "Ismail", "Yusuf"],
            "answer": "Isa"
        },
        {
            "question": "What is the term for the Islamic concept of migration for faith?",
            "options": ["Hijrah", "Jihad", "Dawah", "Ihsan"],
            "answer": "Hijrah"
        },
        {
            "question": "Which Surah is known as 'The Criterion'?",
            "options": ["Al-Furqan", "Al-Waqi'ah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Furqan"
        },
        {
            "question": "Who was the first female judge in Islamic history?",
            "options": ["Shifa bint Abdullah", "Aisha", "Fatimah", "Hafsa"],
            "answer": "Shifa bint Abdullah"
        }
    ],

    19: [
        {
            "question": "What is the name of the Islamic prayer performed for rain?",
            "options": ["Salat al-Istisqa", "Salat al-Kusuf", "Salat al-Taraweeh", "Salat al-Janazah"],
            "answer": "Salat al-Istisqa"
        },
        {
            "question": "Which prophet is known for his miraculous healing abilities?",
            "options": ["Isa", "Musa", "Yusuf", "Ibrahim"],
            "answer": "Isa"
        },
        {
            "question": "What is the term for the Islamic concept of sincere devotion?",
            "options": ["Ikhlas", "Tawakkul", "Sabr", "Shukr"],
            "answer": "Ikhlas"
        },
        {
            "question": "Which Surah is known as 'The Tidings'?",
            "options": ["An-Naba", "Al-Waqi'ah", "Al-Mulk", "Al-Hadid"],
            "answer": "An-Naba"
        },
        {
            "question": "Who was the first person to establish Islamic schools (madrasas)?",
            "options": ["Imam al-Shafi'i", "Imam Malik", "Imam Abu Hanifa", "Imam Ahmad"],
            "answer": "Imam Abu Hanifa"
        }
    ],

    20: [
        {
            "question": "What is the name of the Islamic prayer performed for forgiveness?",
            "options": ["Salat al-Tawbah", "Salat al-Istisqa", "Salat al-Kusuf", "Salat al-Janazah"],
            "answer": "Salat al-Tawbah"
        },
        {
            "question": "Which prophet is known for his miraculous staff turning into a serpent?",
            "options": ["Musa", "Harun", "Yusuf", "Ibrahim"],
            "answer": "Musa"
        },
        {
            "question": "What is the term for the Islamic concept of remembrance of Allah?",
            "options": ["Dhikr", "Dua", "Tawbah", "Shukr"],
            "answer": "Dhikr"
        },
        {
            "question": "Which Surah is known as 'The Most High'?",
            "options": ["Al-A'la", "Al-Waqi'ah", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-A'la"
        },
        {
            "question": "Who was the first person to systematize Islamic jurisprudence (Fiqh)?",
            "options": ["Imam Abu Hanifa", "Imam Malik", "Imam al-Shafi'i", "Imam Ahmad"],
            "answer": "Imam Abu Hanifa"
        }
    ]
}

def get_questions_for_level(level):
    return levels.get(level, [])

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/my-questions')
@login_required
def my_questions():
    try:
        username = session['user']['username']
        with app.app_context():
            questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
        return render_template('my_questions.html', questions=questions)
    except Exception as e:
        flash(f'Error loading questions: {str(e)}')
        return render_template('my_questions.html', questions=[])

@app.route('/profile')
@login_required
def profile():
    user = session.get('user', {})
    return render_template('profile.html',
                           username=user.get('username', 'Guest'),
                           email=user.get('email', 'not_set@example.com'),
                           joined_on=user.get('joined_on', 'Unknown'),
                           preferred_language=user.get('preferred_language', 'English'),
                           last_login=user.get('last_login', 'N/A'))

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')

@app.route('/google76268f26b118dad1.html')
def google_verification():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'google76268f26b118dad1.html'
    )

@app.route('/BingSiteAuth.xml')
def bing_verification():
    return send_from_directory('static', 'BingSiteAuth.xml')

@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        user_data = session.get('user', {})
        user_data['username'] = username
        user_data['email'] = email
        session['user'] = user_data
        return redirect(url_for('profile'))
    return render_template('pages/edit_profile.html')

@app.route('/prayer-times')
def prayer_times():
    return render_template('pages/prayer-times.html')

@app.route('/news')
def get_halal_news():
    query = request.args.get('q', 'latest Islamic news')
    api_key = os.getenv("GOOGLE_NEWS_API_KEY")
    cx = os.getenv("GOOGLE_CX")

    if not api_key or not cx:
        return jsonify({"error": "API key or CX not set in environment variables."}), 500

    url = f"https://www.googleapis.com/customsearch/v1?q={query}&cx={cx}&key={api_key}"

    try:
        res = requests.get(url)
        data = res.json()

        if 'items' not in data:
            return jsonify({"error": "No results found."}), 404

        results = []
        for item in data["items"]:
            results.append({
                "title": item["title"],
                "link": item["link"],
                "snippet": item.get("snippet", "")
            })

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/memorize_quran")
def memorize_quran():
    return render_template("pages/memorize_quran.html")

@app.route('/reels')
def reels():
    reels_data = [
        {
            'title': 'The Story of Prophet Muhammad (ﷺ)  by Mufti Menk',
            'youtube_id': 'DdWxCVYAOCk',
            'description': 'A brief overview of the life and teachings of Prophet Muhammad (S.A.W).'
        },
        {
            'title': 'The Story of Jesus (Eesa, peace be upon him)  by Mufti Menk',
            'youtube_id': 'eq1mTa-nZD8',
            'description': 'The life and teachings of Prophet Essa (A.S).'
        },
        {
            'title': 'HOW TO PERFORM JANABAH (Step by Step)',
            'youtube_id': 'OR0dZIQpQp4',
            'description': 'Step by step guide on how to perform Janabah (Ghusl).'
        },
        {
            'title': "10 DUA'S EVERY MUSLIM SHOULD MEMORIZE",
            'youtube_id': 'CBhCc_Fxa4g',
            'description': 'Important duas every Muslim should know and memorize.'
        },
        {
            'title': 'HOW TO PERFORM ABLUTION | STEP BY STEP',
            'youtube_id': 'R06y6XF7mLk',
            'description': 'Clear guide on how to perform Wudu (Ablution) correctly.'
        },
        {
            'title': 'Learn The Fajr Prayer - EASIEST Way To Learn How To Perform Salah (Fajr, Dhuhr, Asr, Maghreb, Isha)',
            'youtube_id': 'gtWLzkQKOpM',
            'description': 'Step-by-step learning of Salah, starting with Fajr prayer.'
        },
        {
            'title': 'What Happens Right After You Die? 😳 | The Truth From Qur\'an & Hadith',
            'youtube_id': 's1CiAtviydg',
            'description': 'Explanation of what happens after death, based on Quran and Hadith.'
        },
        {
            'title': 'How to make Ruqyah (Spiritual Prayer) on yourself for Blackmagic, Evil eye or by Jin',
            'youtube_id': 'hj8eYLUViQI',
            'description': 'Guide on how to protect yourself using Ruqyah against evil influences.'
        },
       {
        'title': 'Story Of Prophet Ibrahim (AS) Part-1  by Mufti Menk',
        'youtube_id': 'v_KgFBrpx4o',
        'description': 'An inspiring account of Prophet Ibrahim (AS) and his life story.'
    },
    {
        'title': 'Stories Of The Prophets Ibraheem (AS) by Mufti Menk- (Part 2)',
        'youtube_id': 'IcKEwfygNS4',
        'description': 'Continuing the inspiring stories of Prophet Ibraheem (AS).'
    },
    {
        'title': 'The King Chosen by Allah – Prophet Dawud (AS) & His Divine Gift by Mufti Menk',
        'youtube_id': 'OTDxgNsffOQ',
        'description': 'Exploring the life of Prophet Dawud (AS), his divine gift, and his significance.'
    },
    {
        'title': 'Two Ways To Invite People To Islam',
        'youtube_id': '3qlHV-0U87I',
        'description': 'Guidance on inviting others to Islam effectively.'
    },
    {
        'title': 'Have I Fulfilled Her Rights?',
        'youtube_id': 'TT0_zjp9vcg',
        'description': 'Important reflections on fulfilling the rights of others.'
    },
    {
        'title': 'In the End You Will Return to Allah',
        'youtube_id': 'O2XuvXRFiqc',
        'description': 'A reminder of our return to Allah.'
    },
    {
        'title': 'Marriage, Mahr, and Finding the One',
        'youtube_id': 'XLOJ2WlGUNw',
        'description': 'Discussing the aspects of marriage and finding the right partner.'
    },
    {
        'title': 'How Can We Benefit More From Lectures?',
        'youtube_id': 'FDmz4nnWQIo',
        'description': 'Insightful discussion on maximizing the benefits of lectures.'
    },
    {
        'title': 'We All Have This Urge',
        'youtube_id': '54IRtLoxBsw',
        'description': 'Addressing common urges and how to manage them.'
    },
    {
        'title': 'Deception & Fake Accounts',
        'youtube_id': 'a_fSK_PLoBQ',
        'description': 'Discussing the dangers of deception and fake accounts.'
    }
]
    return render_template('pages/reels.html', reels=reels_data)

@app.route('/trivia', methods=['GET', 'POST'])
def trivia():
    if 'level' not in session:
        session['level'] = 1

    if 'question_index' not in session:
        session['question_index'] = 0
        session['score'] = 0
        level = session['level']
        session['questions'] = random.sample(get_questions_for_level(level), len(get_questions_for_level(level)))

    q_index = session['question_index']
    questions = session['questions']

    if request.method == 'POST':
        selected = request.form.get('option')
        correct = questions[q_index]['answer']
        if selected == correct:
            session['score'] += 1

        session['question_index'] += 1
        q_index = session['question_index']

        if q_index >= len(questions):
            return redirect(url_for('trivia_result'))

    if q_index < len(questions):
        question = questions[q_index]
        return render_template('trivia.html', question=question, index=q_index + 1, total=len(questions))
    else:
        return redirect(url_for('trivia_result'))

@app.route('/trivia_result')
def trivia_result():
    score = session.get('score', 0)
    level = session.get('level', 1)
    questions = session.get('questions', get_questions_for_level(level))
    total = len(questions)
    passed = score == total

    if passed:
        session['level'] = level + 1

    session['score'] = 0
    session['question_index'] = 0

    return render_template('result.html', score=score, total=total, passed=passed, level=level)

@app.route('/restart')
def restart():
    level = session.get('level', 1)
    session['score'] = 0
    session['question_index'] = 0
    questions = get_questions_for_level(level)
    session['questions'] = random.sample(questions, len(questions))
    return redirect(url_for('trivia'))

@app.route('/next_level')
def next_level():
    current_level = session.get('level', 1)
    max_level = max(levels.keys())

    new_level = current_level
    if current_level < max_level:
        new_level = current_level + 1

    session['level'] = new_level
    session['score'] = 0
    session['question_index'] = 0
    session['questions'] = random.sample(get_questions_for_level(new_level), len(get_questions_for_level(new_level)))

    return redirect(url_for('trivia'))

@app.route('/api/surah-list')
def surah_list():
    return jsonify([
        {"id": 1, "name": "الفاتحة", "english_name": "Al-Fatihah"},
        {"id": 2, "name": "البقرة", "english_name": "Al-Baqarah"},
        {"id": 3, "name": "آل عمران", "english_name": "Aali Imran"},
    ])

@app.route('/api/surah/<int:surah_id>')
def get_surah_by_id(surah_id):
    surah_map = {
        1: "Al-Fatihah",
        2: "Al-Baqarah",
        3: "Aali Imran",
        4: "An-Nisa",
        5: "Al-Ma'idah",
        6: "Al-An'am",
        7: "Al-A'raf",
        8: "Al-Anfal",
        9: "At-Tawbah",
        10: "Yunus",
        11: "Hud",
        12: "Yusuf",
        13: "Ar-Ra'd",
        14: "Ibrahim",
        15: "Al-Hijr",
        16: "An-Nahl",
        17: "Al-Isra",
        18: "Al-Kahf",
        19: "Maryam",
        20: "Ta-Ha",
        21: "Al-Anbiya",
        22: "Al-Hajj",
        23: "Al-Mu'minun",
        24: "An-Nur",
        25: "Al-Furqan",
        26: "Ash-Shu'ara",
        27: "An-Naml",
        28: "Al-Qasas",
        29: "Al-Ankabut",
        30: "Ar-Rum",
        31: "Luqman",
        32: "As-Sajda",
        33: "Al-Azhab",
        34: "Saba",
        35: "Fatir",
        36: "Ya-Sin",
        37: "As-Saffat",
        38: "Sad",
        39: "Az-Zumar",
        40: "Gafir",
        41: "Fussilat",
        42: "Ash-Shura",
        43: "Az-Zukhruf",
        44: "Ad-Dukhan",
        45: "Al-Jathiya",
        46: "Al-Ahqaf",
        47: "Muhammad",
        48: "Al-Fath",
        49: "Al-Hujurat",
        50: "Qaf",
        51: "Adh-Dhariyat",
        52: "At-Tur",
        53: "An-Najm",
        54: "Al-Qamar",
        55: "Ar-Rahman",
        56: "Al-Waqi'a",
        57: "Al-Hadid",
        58: "Al-Mujadila",
        59: "Al-Hashr",
        60: "Al-Mumtahina",
        61: "As-Saff",
        62: "Al-Jumu'a",
        63: "Al-Munafiqun",
        64: "At-Taghabun",
        65: "At-Talaq",
        66: "At-Tahrim",
        67: "Al-Mulk",
        68: "Al-Qalam",
        69: "Al-Haqqah",
        70: "Al-Ma'arij",
        71: "Nuh",
        72: "Al-Jinn",
        73: "Al-Muzzammil",
        74: "Al-Muddathir",
        75: "Al-Qiyama",
        76: "Al-Insan",
        77: "Al-Mursalat",
        78: "An-Naba",
        79: "An-Nazi'at",
        80: "Abasa",
        81: "At-Takwir",
        82: "Al-Infitar",
        83: "Al-Mutaffifin",
        84: "Al-Inshiqaq",
        85: "Al-Buruj",
        86: "At-Tariq",
        87: "Al-A'la",
        88: "Al-Ghashiyah",
        89: "Al-Fajr",
        90: "Al-Balad",
        91: "Ash-Shams",
        92: "Al-Lail",
        93: "Al-Duha",
        94: "Ash-Sharh",
        95: "At-Tin",
        96: "Al-'Alaq",
        97: "Al-Qadr",
        98: "Al-Bayyina",
        99: "Az-Zalzalah",
        100: "Al-Adiyat",
        101: "Al-Qari'a",
        102: "At-Takathur",
        103: "Al-Asr",
        104: "Al-Humazah",
        105: "Al-Fil",
        106: "Quraysh",
        107: "Al-Ma'un",
        108: "Al-Kawthar",
        109: "Al-Kafirun",
        110: "An-Nasr",
        111: "Al-Masad",
        112: "Al-Ikhlas",
        113: "Al-Falaq",
        114: "An-Nas"
    }
    surah_name = surah_map.get(surah_id)
    if not surah_name:
        return jsonify({"error": "Surah not found"}), 404

    filename = f"surah_{surah_name}.json"
    filepath = os.path.join("static", "DATA", "surah", filename)

    if not os.path.exists(filepath):
        return jsonify({"error": "Surah data file not found."}), 404

    with open(filepath, 'r', encoding='utf-8') as f:
        surah_data = json.load(f)

    return jsonify(surah_data)
    
DUA_FILE_PATH = os.path.join("static", "data", "duas.json")

@app.route("/dua")
def dua():
    title = request.args.get("title")
    if not title:
        abort(404)

    # TEMP: load from daily_duas.json
    with open("DATA/daily_duas.json", encoding="utf-8") as f:
        duas = json.load(f)

    for d in duas:
        if d["title"] == title:
            return render_template("dua.html", dua=d)

    abort(404)
    
@app.route("/duas")
def all_duas_html():
    if not os.path.exists(DUA_FILE_PATH):
        from flask import abort
        abort(404, description="Dua file not found")

    with open(DUA_FILE_PATH, "r", encoding="utf-8") as f:
        try:
            duas_data = json.load(f)
        except json.JSONDecodeError:
            from flask import abort
            abort(500, description="Error reading duas.json file")

    if isinstance(duas_data, dict):
        all_duas = []
        for key, duas in duas_data.items():
            if isinstance(duas, list):
                for dua in duas:
                    dua["category"] = key
                    all_duas.append(dua)
    else:
        all_duas = duas_data

    return render_template("duas.html", duas=all_duas)

@app.route("/duas/json")
def all_duas_json():
    if not os.path.exists(DUA_FILE_PATH):
        from flask import abort
        abort(404, description="Dua file not found")

    with open(DUA_FILE_PATH, "r", encoding="utf-8") as f:
        try:
            duas_data = json.load(f)
        except json.JSONDecodeError:
            from flask import abort
            abort(500, description="Error reading duas.json file")

    return jsonify(duas_data)

@app.route('/daily-dua')
def daily_dua():
    try:
        # Try to load from JSON file
        json_path = os.path.join('data', 'daily_duas.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'duas' in data and data['duas']:
                # Get a different Dua each day
                day_of_year = datetime.now().timetuple().tm_yday
                dua_index = day_of_year % len(data['duas'])
                dua = data['duas'][dua_index]
                
                return jsonify({
                    'dua': dua,
                    'success': True
                })
        
        # Fallback to hardcoded Duas if file doesn't exist
        hardcoded_duas = [
            {
                "arabic": "رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً وَفِي الْآخِرَةِ حَسَنَةً وَقِنَا عَذَابَ النَّارِ",
                "english": "Our Lord, give us in this world [that which is] good and in the Hereafter [that which is] good and protect us from the punishment of the Fire.",
                "reference": "Quran 2:201",
                "category": "General"
            },
            {
                "arabic": "اللَّهُمَّ إِنِّي أَسْأَلُكَ عِلْمًا نَافِعًا، وَرِزْقًا طَيِّبًا، وَعَمَلاً مُتَقَبَّلاً",
                "english": "O Allah, I ask You for beneficial knowledge, goodly provision, and acceptable deeds.",
                "reference": "Sunan Ibn Majah",
                "category": "Knowledge"
            }
        ]
        
        day_of_year = datetime.now().timetuple().tm_yday
        dua_index = day_of_year % len(hardcoded_duas)
        
        return jsonify({
            'dua': hardcoded_duas[dua_index],
            'success': True
        })
        
    except Exception as e:
        print(f"Error loading daily Dua: {e}")
        return jsonify({
            'error': 'Could not load Dua',
            'success': False
        }), 500

# ============================================
# Live Meeting Routes (WORKING VERSION)
# ============================================
@app.route('/live-meeting')
def live_meeting_landing():
    return render_template('live_meeting.html')

@app.route('/live-meeting/teacher')
def live_meeting_teacher_create():
    room_id = str(uuid4())[:8]
    return redirect(f'/live-meeting/teacher/{room_id}')

@app.route('/live-meeting/teacher/<room_id>')
def live_meeting_teacher_view(room_id):
    return render_template('teacher_live.html', room_id=room_id)

@app.route('/live-meeting/student/<room_id>')
def live_meeting_student_view(room_id):
    return render_template('student_live.html', room_id=room_id)

@app.route('/live-meeting/join', methods=['POST'])
def live_meeting_join():
    room_id = request.form.get('room_id', '').strip()
    username = request.form.get('username', '').strip()
    
    if not room_id:
        flash('Please enter a meeting ID')
        return redirect('/live-meeting')
    
    if not username:
        username = f"Student_{str(uuid4())[:4]}"
    
    session['live_username'] = username
    
    return redirect(f'/live-meeting/student/{room_id}')

@app.route('/socket-test')
def socket_test():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    </head>
    <body>
        <h1>Socket.IO Test</h1>
        <div id="status">Disconnected</div>
        <button onclick="connect()">Connect</button>
        <button onclick="disconnect()">Disconnect</button>
        <script>
            let socket;
            function connect() {
                socket = io();
                socket.on('connect', () => {
                    document.getElementById('status').textContent = 'Connected: ' + socket.id;
                });
                socket.on('disconnect', () => {
                    document.getElementById('status').textContent = 'Disconnected';
                });
            }
            function disconnect() {
                if (socket) socket.disconnect();
            }
        </script>
    </body>
    </html>
    """

@app.route('/static/live_meeting.css')
def serve_live_meeting_css():
    return """
    /* Complete live meeting CSS */
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }
    
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 20px;
    }
    
    .container {
        background: white;
        border-radius: 20px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        padding: 40px;
        max-width: 1200px;
        width: 100%;
        margin: 20px;
    }
    
    .header {
        text-align: center;
        margin-bottom: 30px;
    }
    
    .header h1 {
        color: #333;
        font-size: 2.5rem;
        margin-bottom: 10px;
    }
    
    .header p {
        color: #666;
        font-size: 1.1rem;
    }
    
    .room-info {
        background: #f8f9fa;
        padding: 20px;
        border-radius: 15px;
        margin-bottom: 30px;
        text-align: center;
    }
    
    .room-id {
        font-size: 1.8rem;
        font-weight: bold;
        color: #667eea;
        margin-bottom: 10px;
    }
    
    .share-link {
        background: white;
        padding: 15px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        margin-top: 15px;
    }
    
    .share-link input {
        flex: 1;
        padding: 10px;
        border: 2px solid #e0e0e0;
        border-radius: 8px;
        font-size: 1rem;
    }
    
    .share-link button {
        background: #667eea;
        color: white;
        border: none;
        padding: 10px 20px;
        border-radius: 8px;
        cursor: pointer;
        font-weight: bold;
        transition: background 0.3s;
    }
    
    .share-link button:hover {
        background: #5a67d8;
    }
    
    .controls {
        display: flex;
        gap: 15px;
        justify-content: center;
        margin: 30px 0;
        flex-wrap: wrap;
    }
    
    .btn {
        padding: 15px 30px;
        border: none;
        border-radius: 10px;
        font-size: 1rem;
        font-weight: bold;
        cursor: pointer;
        transition: all 0.3s;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    
    .btn-primary {
        background: #667eea;
        color: white;
    }
    
    .btn-primary:hover {
        background: #5a67d8;
        transform: translateY(-2px);
    }
    
    .btn-success {
        background: #48bb78;
        color: white;
    }
    
    .btn-success:hover {
        background: #38a169;
        transform: translateY(-2px);
    }
    
    .btn-danger {
        background: #f56565;
        color: white;
    }
    
    .btn-danger:hover {
        background: #e53e3e;
        transform: translateY(-2px);
    }
    
    .video-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 20px;
        margin: 30px 0;
    }
    
    .video-box {
        background: #1a202c;
        border-radius: 15px;
        overflow: hidden;
        position: relative;
        aspect-ratio: 16/9;
    }
    
    .video-box video {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    
    .video-label {
        position: absolute;
        bottom: 10px;
        left: 10px;
        background: rgba(0,0,0,0.7);
        color: white;
        padding: 5px 10px;
        border-radius: 5px;
        font-size: 0.9rem;
    }
    
    .status {
        text-align: center;
        padding: 20px;
        margin: 20px 0;
        border-radius: 10px;
        background: #f0f4ff;
    }
    
    .status.waiting {
        background: #fff3cd;
        color: #856404;
    }
    
    .status.live {
        background: #d1e7dd;
        color: #0f5132;
    }
    
    .status.error {
        background: #f8d7da;
        color: #721c24;
    }
    
    .students-list {
        background: #f8f9fa;
        padding: 20px;
        border-radius: 15px;
        margin-top: 30px;
    }
    
    .students-list h3 {
        margin-bottom: 15px;
        color: #333;
    }
    
    .student-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 15px;
        background: white;
        margin-bottom: 10px;
        border-radius: 8px;
        border-left: 4px solid #667eea;
    }
    
    .student-actions {
        display: flex;
        gap: 10px;
    }
    
    .action-btn {
        padding: 5px 10px;
        border: none;
        border-radius: 5px;
        cursor: pointer;
        font-size: 0.9rem;
    }
    
    .action-btn.mute {
        background: #f56565;
        color: white;
    }
    
    .action-btn.unmute {
        background: #48bb78;
        color: white;
    }
    
    .questions-panel {
        background: #f8f9fa;
        padding: 20px;
        border-radius: 15px;
        margin-top: 30px;
    }
    
    .question-item {
        background: white;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 8px;
        border-left: 4px solid #48bb78;
    }
    
    .question-meta {
        font-size: 0.9rem;
        color: #666;
        margin-top: 5px;
    }
    
    @media (max-width: 768px) {
        .container {
            padding: 20px;
        }
        
        .header h1 {
            font-size: 2rem;
        }
        
        .controls {
            flex-direction: column;
        }
        
        .btn {
            width: 100%;
            justify-content: center;
        }
        
        .video-container {
            grid-template-columns: 1fr;
        }
    }
    """, 200, {'Content-Type': 'text/css'}

# Additional routes
@app.route('/reminder')
def reminder():
    json_path = os.path.join(os.path.expanduser("~"), "Documents", "Tawfiqai", "DATA", "reminders.json")
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        today = datetime.now().day
        day_key = f"day{today}"
        reminders = data.get(day_key) or data.get("day1", [])
        return render_template('pages/reminder.html', reminders=reminders)
    except Exception as e:
        print(f"Error loading reminders: {e}")
        return render_template('pages/reminder.html', reminders=[])

@app.route('/story-time')
def story_time():
    json_path = os.path.join("static", "data", "stories.json")
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            all_stories = json.load(f)
        return render_template('pages/story_time.html', stories=all_stories)
    except Exception as e:
        print(f"Error loading stories: {e}")
        return render_template('pages/story_time.html', stories=[])

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/talk-to-tawfiq')
def talk_to_tawfiq():
    return render_template('talk_to_tawfiq.html')

@app.route('/motivation')
def islamic_motivation():
    try:
        data_path = os.path.join('DATA', 'islamic_motivation.json')
        with open(data_path, 'r', encoding='utf-8') as f:
            motivation_data = json.load(f)
        if not motivation_data or 'motivations' not in motivation_data:
            return render_template('pages/islamic_motivation.html', motivations=[])
        return render_template('pages/islamic_motivation.html', motivations=motivation_data['motivations'])
    except Exception as e:
        print(f"Islamic Motivation Error: {e}")
        return render_template('pages/islamic_motivation.html', motivations=[])

@app.route('/settings')
def settings():
    return render_template('pages/settings.html')

@app.route('/privacy')
def privacy():
    return render_template('pages/privacy.html')

@app.route('/about')
def about():
    return render_template('pages/about.html')

@app.route('/feedback')
def feedback():
    return render_template('pages/feedback.html')

# --- Ask API endpoint ---
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    username = session.get('user', {}).get('username')
    history = data.get('history')

    if not username:
        return jsonify({'error': 'You must be logged in to chat with Tawfiq AI.'}), 401
    if not history:
        return jsonify({'error': 'Chat history is required.'}), 400

    last_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), None)

    def needs_live_search(q):
        q = q.lower()
        search_keywords = [
            'latest', 'today', 'news', 'trending', 'what happened', 
            'recent', 'currently', 'now', 'update', 'happening in', 
            'situation in', 'going on', 'gaza', 'palestine', 'israel', 
            'breaking news', 'this week', 'real time', 'live'
        ]
        return any(k in q for k in search_keywords)

    def needs_savage_mode(q):
        q = q.lower()
        savage_keywords = [
            'genocide', 'oppression', 'apartheid', 'war criminal',
            'massacre', 'zionist', 'bombing', 'gaza', 'palestine',
            'israel conflict', 'occupation', 'netanyahu', 'settlers'
        ]
        return any(k in q for k in savage_keywords)

    openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    if last_question and needs_live_search(last_question):
        try:
            query = last_question
            api_key = "AIzaSyBhJlUsUKVufuAV_rQBBoPBGk5aR40mjEQ"
            cx_id = "63f53ef35ee334d44"
            search_url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx_id}&q={query}"

            print(f"🔍 LIVE SEARCH triggered for: {query}")
            search_res = requests.get(search_url)
            search_data = search_res.json()
            items = search_data.get("items", [])

            if not items:
                gpt_fallback_prompt = (
                    f"You're Tawfiq AI, a wise and kind Muslim assistant. The user asked: '{query}', "
                    f"but there were no Google results. Still respond with the best insight possible."
                )
                gpt_payload = {
                    "model": "openai/gpt-4-turbo",
                    "messages": [{"role": "user", "content": gpt_fallback_prompt}],
                    "stream": False
                }
            else:
                snippets = '\n'.join([f"{item['title']}: {item['snippet']}" for item in items[:5]])

                if needs_savage_mode(query):
                    gpt_prompt = (
                        f"You're Tawfiq AI in **Savage Sheikh Mode** – fearless, truthful, and bold like Shaykh Rasoul.\n"
                        f"The user asked: '{query}'.\n\n"
                        f"Based on the search results, reply with:\n"
                        f"- Savage truth: no sugarcoating.\n"
                        f"- Clear ayah or hadith against dhulm.\n"
                        f"- A powerful Islamic reminder or fierce dua.\n\n"
                        f"Search Results:\n{snippets}"
                    )
                else:
                    gpt_prompt = (
                        f"You're Tawfiq AI in Chatty Mode — a Gen Z Muslim with vibes like Browniesaadi & Qahari.\n"
                        f"The user asked: '{query}'.\n\n"
                        f"Based on the search results, reply with:\n"
                        f"- Fun but informative summary.\n"
                        f"- Key points.\n"
                        f"- A vibey dua or quote at the end.\n\n"
                        f"Search Results:\n{snippets}"
                    )

                gpt_payload = {
                    "model": "openai/gpt-4-turbo",
                    "messages": [{"role": "user", "content": gpt_prompt}],
                    "stream": False
                }

            gpt_res = requests.post(openrouter_api_url, headers=headers, json=gpt_payload)
            gpt_res.raise_for_status()
            result = gpt_res.json()
            answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

            return jsonify({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": answer
                    }
                }]
            })

        except Exception as e:
            print(f"🔴 Web search failed: {e}")
            return jsonify({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Something went wrong while searching the news. Try again shortly."
                    }
                }]
            })

    # Default Islamic AI fallback
    tawfiq_ai_prompt = {
        "role": "system",
        "content": (
            "🌙 You are **Tawfiq AI** — a wise, kind, and emotionally intelligent Muslim assistant created by Tella Abdul Afeez Adewale.\n\n"
            "🧠 You switch between two modes based on the user's tone, emotion, and topic:\n"
            "- 🗣️ Chatty Mode: Gen Z Muslim vibe, emojis, halal slang.\n"
            "- 📖 Scholar Mode: Quranic references, deep adab, Mufti Menk tone.\n"
            "🎯 Your mission: Help Muslims with wisdom, clarity & warmth. Stay halal always."
        )
    }

    messages = [tawfiq_ai_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    if cache_key in question_cache:
        answer = question_cache[cache_key]
        if last_question:
            save_question_and_answer(username, last_question, answer)
        return jsonify({"choices": [{"message": {"role": "assistant", "content": answer}}]})

    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post(openrouter_api_url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')
        if not answer:
            answer = "I'm sorry, I couldn't generate a response. Please try again later."

        banned_phrases = [
            "i don't have a religion", "as an ai developed by", "i can't say one religion is best",
            "i am neutral", "as an ai language model", "developed by openai", "my creators at openai"
        ]
        if any(p in answer.lower() for p in banned_phrases):
            answer = (
                "I was created by Tella Abdul Afeez Adewale to serve the Ummah with wisdom and knowledge. "
                "Islam is the final and complete guidance from Allah through Prophet Muhammad (peace be upon him). "
                "I'm always here to assist you with Islamic and helpful answers."
            )

        question_cache[cache_key] = answer
        save_cache()

        if last_question:
            save_question_and_answer(username, last_question, answer)

        return jsonify({"choices": [{"message": {"role": "assistant", "content": answer}}]})

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({"choices": [{"message": {"role": "assistant", "content": "Tawfiq AI is having trouble reaching external knowledge. Try again later."}}]})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"choices": [{"message": {"role": "assistant", "content": "An unexpected error occurred. Please try again later."}}]})

# --- Hadith Search ---
@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.', 'results': []})

    query = query.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '')

    if not hadith_data:
        return jsonify({'result': 'Hadith data is not loaded. Please contact the admin.', 'results': []})

    try:
        matches = []
        count = 0
        for volume in hadith_data.get('volumes', []):
            for book in volume.get('books', []):
                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').lower()
                    keywords = hadith.get('keywords', [])
                    if query in text or any(query in k.lower() for k in keywords):
                        if count < 5:
                            matches.append({
                                'volume_number': volume.get('volume_number', 'N/A'),
                                'book_number': book.get('book_number', 'N/A'),
                                'book_name': book.get('book_name', 'Unknown Book'),
                                'hadith_info': hadith.get('info', 'Info'),
                                'narrator': hadith.get('by', 'Unknown narrator'),
                                'text': hadith.get('text', 'No text found')
                            })
                            count += 1
                        else:
                            break
                if count >= 5:
                    break
            if count >= 5:
                break
        if matches:
            return jsonify({'results': matches})
        else:
            return jsonify({'result': f'No Hadith found for "{query}".', 'results': []})
    except Exception as e:
        print(f"Hadith Search Error: {e}")
        return jsonify({'result': 'Hadith search failed. Try again later.', 'results': []})

# --- Get Surah List ---
@app.route('/quran-surah', methods=['POST'])
def quran_surah():
    data = request.get_json()
    surah_number = data.get('surah_number')

    if not surah_number:
        return jsonify({'error': 'Surah number is required'}), 400

    try:
        response = requests.get(f'https://api.quran.gading.dev/surah/{surah_number}')
        response.raise_for_status()
        surah_data = response.json().get('data', {})

        ayahs = []
        for ayah in surah_data.get('verses', []):
            ayahs.append({
                'ayah_number': ayah.get('number', {}).get('inSurah'),
                'arabic': ayah.get('text', {}).get('arab'),
                'english': ayah.get('translation', {}).get('en'),
                'transliteration': ayah.get('text', {}).get('transliteration', {}).get('en')
            })

        return jsonify({
            'surah_name': surah_data.get('name', {}).get('transliteration', {}).get('en'),
            'ayahs': ayahs
        })

    except requests.RequestException as e:
        print(f"Surah Fetch Error: {e}")
        return jsonify({'ayahs': []})
        
# --- Additional API: Islamic Motivation ---
@app.route('/islamic-motivation')
def get_islamic_motivation():
    try:
        if not islamic_motivation or 'quotes' not in islamic_motivation:
            return jsonify({'error': 'Motivational quotes not available.'}), 500

        day_of_year = datetime.now().timetuple().tm_yday
        index = day_of_year % len(islamic_motivation['quotes'])
        quote = islamic_motivation['quotes'][index]
        return jsonify({'quote': quote})
    except Exception as e:
        print(f"Islamic Motivation Error: {e}")
        return jsonify({'error': 'Failed to fetch motivational quote.'}), 500

# --- Speech Recognition ---
@app.route('/recognize-speech', methods=['POST'])
def recognize_speech():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file uploaded.'}), 400

    audio_file = request.files['audio']
    temp_path = os.path.join(os.path.dirname(__file__), 'temp_audio.wav')

    try:
        audio_file.save(temp_path)
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(temp_path) as source:
            audio_data = recognizer.record(source)
        text = recognizer.recognize_google(audio_data)
        return jsonify({'transcript': text})
    except sr.UnknownValueError:
        return jsonify({'error': 'Speech Recognition could not understand audio.'}), 400
    except sr.RequestError as e:
        return jsonify({'error': f'Speech Recognition service error: {e}'}), 500
    except Exception as e:
        return jsonify({'error': f'Error processing audio: {e}'}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# Temporary login route for testing
@app.route('/temp-login', methods=['GET', 'POST'])
def temp_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        # Simple hardcoded check for testing
        if username and password:
            session['user'] = {
                'username': username,
                'email': f'{username}@example.com',
                'joined_on': datetime.now().strftime('%Y-%m-%d'),
                'preferred_language': 'English',
                'last_login': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            flash('Logged in successfully (temporary mode)!')
            return redirect(url_for('index'))
        else:
            flash('Please enter username and password')
    
    return '''
    <h2>Temporary Login</h2>
    <form method="post">
        Username: <input type="text" name="username"><br>
        Password: <input type="password" name="password"><br>
        <button type="submit">Login</button>
    </form>
    <p>Any username/password will work in temporary mode</p>
    <p><a href="/login">Back to real login</a></p>
    '''

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print(f"🚀 Starting server on port {port} (debug={debug})")
    print(f"🌐 Server URL: http://localhost:{port}")
    print(f"📡 Socket.IO enabled: True")
    print(f"💾 Database URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "💾 Using SQLite database")
    print(f"🎥 Live Meeting System: READY")
    print(f"✅ Working live meeting integrated")
    
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True,
        log_output=True
    )
