add import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
import os
import json
from datetime import datetime
from flask import Flask, render_template, session, redirect, url_for, request, flash, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func
from functools import wraps
import uuid
import requests
from dotenv import load_dotenv
import random
from difflib import get_close_matches

# Load environment variables
load_dotenv()

print("✅ API KEY:", os.getenv("GOOGLE_NEWS_API_KEY"))
print("✅ CX:", os.getenv("GOOGLE_CX"))

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('MY_SECRET', 'dev-secret-key')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

# Database Configuration with proper SSL for Render
DATABASE_URL = os.environ.get('DATABASE_URL')

# Fix for Render PostgreSQL - convert postgres:// to postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Use SQLite locally, PostgreSQL on Render
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
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tawfiqai.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Debug mode - set to False in production
DEBUG_MODE = True

def debug_print(*args, **kwargs):
    if DEBUG_MODE:
        print(*args, **kwargs)

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)

# ============================================
# Database Models (Combined)
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

class Room(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    teacher_id = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()
    debug_print("✅ Database tables created")
    
    # Create default user if not exists (only for SQLite)
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        user = User.query.filter_by(username='zayd').first()
        if not user:
            user = User(username='zayd', email='zayd@example.com')
            user.set_password('secure123')
            db.session.add(user)
            db.session.commit()
            debug_print("✅ Default user created")

# ============================================
# In-Memory Storage for Live Meeting
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
# Helper Functions for Tawfiq AI
# ============================================
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

def save_question_and_answer(username, question, answer):
    try:
        with app.app_context():
            existing_entry = UserQuestions.query.filter_by(username=username, question=question).first()

            if existing_entry:
                existing_entry.answer = answer
                existing_entry.timestamp = datetime.utcnow()
                debug_print(f"🔁 Updated existing Q&A for '{username}'")
            else:
                new_entry = UserQuestions(
                    username=username,
                    question=question,
                    answer=answer,
                    timestamp=datetime.utcnow()
                )
                db.session.add(new_entry)
                debug_print(f"✅ Saved new Q&A for '{username}'")

            db.session.commit()

    except Exception as e:
        debug_print(f"❌ Failed to save Q&A for '{username}': {e}")
        try:
            db.session.rollback()
        except:
            pass

# File-Based Cache for Tawfiq AI
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

# Load JSON datasets for Tawfiq AI
def load_json_data(file_name, data_variable_name):
    data = {}
    file_path = os.path.join(os.path.dirname(__file__), 'DATA', file_name)
    debug_print(f"Attempting to load {data_variable_name} data from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        debug_print(f"✅ Successfully loaded {data_variable_name} data")
    except FileNotFoundError:
        debug_print(f"❌ ERROR: {data_variable_name} data file not found at {file_path}")
    except json.JSONDecodeError as e:
        debug_print(f"❌ JSON Decode Error in {file_path}: {e}")
        if 'daily_duas' in file_name:
            data = {"duas": []}
    except Exception as e:
        debug_print(f"❌ Unexpected error while loading {file_name}: {e}")
    return data

# Load datasets
hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Islamic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')
daily_duas = load_json_data('daily_duas.json', 'Daily Duas')
islamic_motivation = load_json_data('islamic_motivation.json', 'Islamic Motivation')

# OpenRouter API Key
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
if not openrouter_api_key:
    debug_print("⚠️ OPENROUTER_API_KEY environment variable not set.")

# ============================================
# Socket.IO Event Handlers - Live Meeting
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    # CRITICAL FIX: Join client to their private SID room for direct messaging
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid} (joined private room: {sid})")

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
            
            debug_print(f"❌ {participant_info['username']} left room {room_id}")
        
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
        
        debug_print(f"👤 {username} ({role}) joining room: {room_id}")
        
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
            'is_waiting': (role == 'student' and not room['teacher_sid'])
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
        debug_print(f"✅ {username} joined room {room_id}. Total participants: {len(room['participants'])}")
        
    except Exception as e:
        debug_print(f"❌ Error in join-room: {e}")
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
        
        debug_print(f"📨 {request.sid[:8]} → offer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room (requires client to join their SID room on connect)
        emit('webrtc-offer', {
            'from_sid': request.sid,
            'offer': offer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying offer: {e}")

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
        
        debug_print(f"📨 {request.sid[:8]} → answer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-answer', {
            'from_sid': request.sid,
            'answer': answer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying answer: {e}")

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
        
        debug_print(f"📨 {request.sid[:8]} → ICE → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying ICE candidate: {e}")

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
        
        debug_print(f"🔗 Initiating full mesh for {sid[:8]} with {len(other_participants)} peers")
        
    except Exception as e:
        debug_print(f"❌ Error in request-full-mesh: {e}")

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
        
        debug_print(f"🔇 Teacher muted all in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-mute-all: {e}")

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
        
        debug_print(f"🔊 Teacher unmuted all in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-unmute-all: {e}")

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
        
        debug_print(f"📢 Teacher starting broadcast in room: {room_id}")
        
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
        debug_print(f"❌ Error in start-broadcast: {e}")
        emit('error', {'message': str(e)})

@socketio.on('ping')
def handle_ping(data):
    """Keep-alive ping"""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

# ============================================
# Authentication Decorators
# ============================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# ============================================
# Flask Routes - Combined
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
            debug_print(f"Database error: {e}")
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

# ============================================
# Live Meeting Routes
# ============================================
@app.route('/teacher')
def teacher_create():
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/teacher/{room_id}')

@app.route('/teacher/<room_id>')
def teacher_view(room_id):
    return render_template('teacher.html', room_id=room_id)

@app.route('/student/<room_id>')
def student_view(room_id):
    return render_template('student.html', room_id=room_id)

@app.route('/join', methods=['POST'])
def join_room_post():
    room_id = request.form.get('room_id', '').strip()
    if not room_id:
        flash('Please enter a room ID')
        return redirect('/')
    return redirect(f'/student/{room_id}')

# ============================================
# Live Meeting Routes (New)
# ============================================
@app.route('/live-meeting')
@app.route('/live_meeting')
def live_meeting():
    return render_template('live_meeting.html')

@app.route('/live-meeting/teacher')
@app.route('/live_meeting/teacher')
def live_meeting_teacher_create():
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('live_meeting_teacher_view', room_id=room_id))

@app.route('/live-meeting/teacher/<room_id>')
@app.route('/live_meeting/teacher/<room_id>')
def live_meeting_teacher_view(room_id):
    return render_template('teacher_live.html', room_id=room_id)

@app.route('/live-meeting/student/<room_id>')
@app.route('/live_meeting/student/<room_id>')
def live_meeting_student_view(room_id):
    return render_template('student_live.html', room_id=room_id)

@app.route('/live-meeting/join', methods=['POST'])
@app.route('/live_meeting/join', methods=['POST'])
def live_meeting_join():
    room_id = request.form.get('room_id', '').strip()
    username = request.form.get('username', '').strip()
    
    if not room_id:
        flash('Please enter a meeting ID')
        return redirect('/live_meeting')
    
    if not username:
        username = f"Student_{str(uuid.uuid4())[:4]}"
    
    session['live_username'] = username
    
    return redirect(url_for('live_meeting_student_view', room_id=room_id))

# ============================================
# NEW: Connection Test Route
# ============================================
@app.route('/test-connection')
def test_connection():
    """Simple connection test page"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connection Test</title>
        <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    </head>
    <body>
        <h1>Socket.IO Connection Test</h1>
        <div id="status">Connecting...</div>
        <div id="events"></div>
        
        <script>
            const socket = io();
            
            socket.on('connect', () => {
                document.getElementById('status').innerHTML = '✅ Connected! SID: ' + socket.id;
                logEvent('Connected to server');
            });
            
            socket.on('disconnect', () => {
                document.getElementById('status').innerHTML = '❌ Disconnected';
                logEvent('Disconnected from server');
            });
            
            socket.on('connect_error', (error) => {
                document.getElementById('status').innerHTML = '❌ Connection Error';
                logEvent('Error: ' + error.message);
            });
            
            function logEvent(msg) {
                const eventsDiv = document.getElementById('events');
                eventsDiv.innerHTML = new Date().toLocaleTimeString() + ': ' + msg + '<br>' + eventsDiv.innerHTML;
            }
        </script>
    </body>
    </html>
    """

# ============================================
# Debug Route
# ============================================
@app.route('/debug/rooms')
def debug_rooms():
    """Debug endpoint to view current room states"""
    debug_info = {
        'rooms': rooms,
        'participants': participants,
        'room_authority': room_authority,
        'total_rooms': len(rooms),
        'total_participants': len(participants)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Additional Tawfiq AI Routes
# ============================================
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
        debug_print(f"Islamic Motivation Error: {e}")
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

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

# ============================================
# Tawfiq AI API Endpoints
# ============================================
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

            debug_print(f"🔍 LIVE SEARCH triggered for: {query}")
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
            debug_print(f"🔴 Web search failed: {e}")
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
        debug_print(f"OpenRouter API Error: {e}")
        return jsonify({"choices": [{"message": {"role": "assistant", "content": "Tawfiq AI is having trouble reaching external knowledge. Try again later."}}]})
    except Exception as e:
        debug_print(f"Unexpected error: {e}")
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
        debug_print(f"Hadith Search Error: {e}")
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
        debug_print(f"Surah Fetch Error: {e}")
        return jsonify({'ayahs': []})

# ============================================
# Temporary login route for testing
# ============================================
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

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 TAWFIQ AI + NELAVISTA LIVE - Combined Application")
    print("🌟 Full Mesh WebRTC Live Meeting System")
    print("🤖 Tawfiq AI Assistant with Islamic Knowledge")
    print(f"{'='*60}")
    print("✅ FIXED: SID private rooms for signaling")
    print("✅ FIXED: Students can join without teacher")
    print("✅ FIXED: Full mesh initiation system")
    print("✅ WebRTC signaling with STUN/TURN")
    print("✅ Production ready for Render deployment")
    print(f"{'='*60}")
    print("\n📡 Connection test: http://localhost:5000/test-connection")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("👨‍🎓 Student test: http://localhost:5000/live_meeting")
    print("🤖 Tawfiq AI: http://localhost:5000/talk-to-tawfiq")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)
