import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
import os
import json
from datetime import datetime
from flask import (
    Flask, render_template, session, redirect, url_for, 
    request, flash, jsonify, send_from_directory, abort
)
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
import uuid
from dotenv import load_dotenv
from hashlib import sha256
import redis
from functools import wraps
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
import random
import requests
import ssl
from werkzeug.utils import secure_filename

# Load environment variables
load_dotenv()

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)

# Database Configuration
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-123')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tawfiq.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Debug mode
DEBUG_MODE = True

def debug_print(*args, **kwargs):
    if DEBUG_MODE:
        print(*args, **kwargs)

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ============================================
# Database Models
# ============================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, default=1)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    profile_picture = db.Column(db.String(200), default='default.png')
    bio = db.Column(db.Text, default='')

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

class GameScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    game_type = db.Column(db.String(50), nullable=False)  # 'trivia', 'memory', 'quiz'
    score = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    played_at = db.Column(db.DateTime, default=datetime.utcnow)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    email = db.Column(db.String(120))
    type = db.Column(db.String(50))  # 'feedback', 'bug', 'suggestion'
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    try:
        db.create_all()
        debug_print("✅ Database tables created successfully")
    except Exception as e:
        debug_print(f"⚠️ Database creation error: {e}")

# ============================================
# In-Memory Storage for Live Meetings
# ============================================
rooms = {}
participants = {}
room_authority = {}
active_rooms = {}
waiting_students = {}
connected_students = {}
student_details = {}

# ============================================
# Helper Functions
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'participants': {},
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
        debug_print(f"⚠️ Database error in get_questions_for_user: {e}")
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

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please login to access this page')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def load_json_data(file_name):
    """Load JSON data from DATA directory"""
    try:
        file_path = os.path.join('DATA', file_name)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # Try static/DATA directory
            file_path = os.path.join('static', 'DATA', file_name)
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                debug_print(f"⚠️ File not found: {file_name}")
                return {}
    except Exception as e:
        debug_print(f"❌ Error loading {file_name}: {e}")
        return {}

# Load JSON data
hadith_data = load_json_data('sahih_bukhari_coded.json')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json')
friendly_responses_data = load_json_data('friendly_responses.json')
daily_duas = load_json_data('daily_duas.json')
islamic_motivation = load_json_data('islamic_motivation.json')
stories_data = load_json_data('stories.json')
reminders_data = load_json_data('reminders.json')

# OpenRouter API Key
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

# ============================================
# Socket.IO Event Handlers
# ============================================

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    
    # Clean up from WebRTC meetings
    participant = participants.get(sid)
    if participant:
        room_id = participant['room_id']
        
        if room_id in rooms:
            room = rooms[room_id]
            
            if sid in room['participants']:
                participant_info = room['participants'][sid]
                del room['participants'][sid]
                
                if sid == room['teacher_sid']:
                    room['teacher_sid'] = None
                    for participant_sid in room['participants']:
                        if room['participants'][participant_sid]['role'] == 'student':
                            emit('teacher-disconnected', room=participant_sid)
                
                emit('participant-left', {
                    'sid': sid,
                    'username': participant_info['username'],
                    'role': participant_info['role']
                }, room=room_id, skip_sid=sid)
            
            cleanup_room(room_id)
    
    # Clean up from live meetings
    for room_id in list(active_rooms.keys()):
        if 'teacher_sid' in active_rooms[room_id] and active_rooms[room_id]['teacher_sid'] == sid:
            emit('room-ended', {
                'room': room_id,
                'teacherId': active_rooms[room_id].get('teacher_id', 'unknown'),
                'reason': 'Teacher disconnected'
            }, room=room_id)
            
            if room_id in active_rooms:
                del active_rooms[room_id]
            if room_id in waiting_students:
                del waiting_students[room_id]
            if room_id in connected_students:
                del connected_students[room_id]
            
        else:
            for user_id, details in list(student_details.items()):
                if details.get('sid') == sid:
                    if room_id in connected_students and user_id in connected_students[room_id]:
                        connected_students[room_id].remove(user_id)
                    
                    if room_id in waiting_students and user_id in waiting_students[room_id]:
                        waiting_students[room_id].remove(user_id)
                    
                    if user_id in student_details:
                        del student_details[user_id]
                    
                    emit('student-left', {
                        'userId': user_id,
                        'username': details.get('username', 'Student')
                    }, room=room_id)
                    break
    
    if sid in participants:
        del participants[sid]

# WebRTC Meeting Handlers
@socketio.on('join-room')
def handle_join_room(data):
    """Join room for WebRTC meetings"""
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
        
        if role == 'teacher' and room['teacher_sid']:
            emit('error', {'message': 'Room already has a teacher'})
            return
        
        room['participants'][sid] = {
            'username': username,
            'role': role,
            'joined_at': datetime.utcnow().isoformat()
        }
        
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
            
            for participant_sid in room['participants']:
                if room['participants'][participant_sid]['role'] == 'student':
                    emit('teacher-joined', {
                        'teacher_sid': sid,
                        'teacher_name': username
                    }, room=participant_sid)
        
        participants[sid] = {
            'room_id': room_id,
            'username': username,
            'role': role
        }
        
        join_room(room_id)
        
        existing_participants = get_participants_list(room_id, exclude_sid=sid)
        
        emit('room-joined', {
            'room': room_id,
            'sid': sid,
            'username': username,
            'role': role,
            'existing_participants': existing_participants,
            'teacher_sid': room['teacher_sid'],
            'is_waiting': (role == 'student' and not room['teacher_sid'])
        })
        
        emit('new-participant', {
            'sid': sid,
            'username': username,
            'role': role
        }, room=room_id, skip_sid=sid)
        
        if role == 'student' and room['teacher_sid']:
            emit('room-state', {
                'muted_all': authority_state['muted_all'],
                'cameras_disabled': authority_state['cameras_disabled'],
                'questions_enabled': authority_state['questions_enabled'],
                'question_visibility': authority_state['question_visibility']
            })
        
        debug_print(f"✅ {username} joined room {room_id}. Total participants: {len(room['participants'])}")
        
    except Exception as e:
        debug_print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# WebRTC Signaling
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        offer = data.get('offer')
        
        if not all([room_id, target_sid, offer]):
            return
        
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        emit('webrtc-offer', {
            'from_sid': request.sid,
            'offer': offer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying offer: {e}")

@socketio.on('webrtc-answer')
def handle_webrtc_answer(data):
    """Relay WebRTC answer to specific participant"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        answer = data.get('answer')
        
        if not all([room_id, target_sid, answer]):
            return
        
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        emit('webrtc-answer', {
            'from_sid': request.sid,
            'answer': answer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying answer: {e}")

@socketio.on('webrtc-ice-candidate')
def handle_webrtc_ice_candidate(data):
    """Relay ICE candidate to specific participant"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        candidate = data.get('candidate')
        
        if not all([room_id, target_sid, candidate]):
            return
        
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying ICE candidate: {e}")

# Live Meeting System Handlers
@socketio.on('teacher-join')
def handle_teacher_join_live(data):
    """Teacher joins live meeting room"""
    try:
        room_id = data.get('room', 'default')
        user_id = data.get('userId', f'teacher_{request.sid}')
        username = data.get('username', 'Teacher')
        
        debug_print(f"👨‍🏫 Teacher {username} ({user_id}) joining room {room_id}")
        
        if room_id not in active_rooms:
            active_rooms[room_id] = {
                'state': 'waiting',
                'teacher_id': user_id,
                'teacher_sid': request.sid,
                'teacher_name': username,
                'connections': [request.sid],
                'created_at': datetime.utcnow().isoformat(),
                'webrtc_started': False
            }
            waiting_students[room_id] = []
            connected_students[room_id] = []
        else:
            active_rooms[room_id]['teacher_sid'] = request.sid
            active_rooms[room_id]['teacher_id'] = user_id
            active_rooms[room_id]['teacher_name'] = username
            if request.sid not in active_rooms[room_id]['connections']:
                active_rooms[room_id]['connections'].append(request.sid)
        
        join_room(room_id)
        
        emit('room-state', {
            'state': active_rooms[room_id]['state'],
            'waitingStudents': len(waiting_students.get(room_id, [])),
            'connectedStudents': len(connected_students.get(room_id, [])),
            'teacherId': user_id,
            'teacherName': username
        })
        
        debug_print(f"✅ Teacher joined room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-join: {e}")
        emit('error', {'message': f'Failed to join room: {str(e)}'})

@socketio.on('student-join')
def handle_student_join_live(data):
    """Student joins live meeting room"""
    try:
        room_id = data.get('room', 'default')
        user_id = data.get('userId', f'student_{request.sid}')
        username = data.get('username', 'Student')
        
        debug_print(f"👨‍🎓 Student {username} ({user_id}) joining room {room_id}")
        
        if room_id not in active_rooms:
            emit('error', {'message': 'Room does not exist or teacher has not joined yet'})
            return
        
        student_details[user_id] = {
            'sid': request.sid,
            'username': username,
            'room': room_id,
            'joined_at': datetime.utcnow().isoformat()
        }
        
        join_room(room_id)
        
        if active_rooms[room_id]['state'] == 'waiting':
            if user_id not in waiting_students[room_id]:
                waiting_students[room_id].append(user_id)
            
            emit('student-waiting', {
                'userId': user_id,
                'username': username,
                'socketId': request.sid
            }, room=room_id, include_self=False)
            
            emit('student-waiting-ack', {
                'status': 'waiting',
                'room': room_id,
                'teacherName': active_rooms[room_id].get('teacher_name', 'Teacher')
            })
            
        else:
            if user_id not in connected_students[room_id]:
                connected_students[room_id].append(user_id)
            
            emit('student-joined', {
                'userId': user_id,
                'username': username,
                'socketId': request.sid
            }, room=room_id, include_self=False)
            
            emit('student-joined-ack', {
                'status': 'joined',
                'room': room_id,
                'teacherName': active_rooms[room_id].get('teacher_name', 'Teacher')
            })
        
        emit('room-state', {
            'state': active_rooms[room_id]['state'],
            'waitingStudents': len(waiting_students.get(room_id, [])),
            'connectedStudents': len(connected_students.get(room_id, [])),
            'teacherId': active_rooms[room_id].get('teacher_id'),
            'teacherName': active_rooms[room_id].get('teacher_name')
        }, room=room_id)
        
    except Exception as e:
        debug_print(f"❌ Error in student-join: {e}")
        emit('error', {'message': f'Failed to join as student: {str(e)}'})

@socketio.on('start-meeting')
def handle_start_meeting(data):
    """Start the live meeting"""
    try:
        room_id = data.get('room', 'default')
        
        if room_id not in active_rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        active_rooms[room_id]['state'] = 'live'
        
        for student_id in waiting_students.get(room_id, []):
            if student_id not in connected_students[room_id]:
                connected_students[room_id].append(student_id)
                
                student_sid = student_details.get(student_id, {}).get('sid')
                if student_sid:
                    emit('meeting-started', {
                        'room': room_id,
                        'teacherId': active_rooms[room_id].get('teacher_id'),
                        'teacherName': active_rooms[room_id].get('teacher_name')
                    }, room=student_sid)
        
        waiting_students[room_id] = []
        
        debug_print(f"🚀 Meeting started in room {room_id} with {len(connected_students.get(room_id, []))} students")
        
        emit('room-started', {
            'room': room_id,
            'teacherId': active_rooms[room_id].get('teacher_id', 'unknown'),
            'teacherName': active_rooms[room_id].get('teacher_name', 'Teacher'),
            'students': [
                {
                    'userId': sid,
                    'username': student_details.get(sid, {}).get('username', 'Student'),
                    'socketId': student_details.get(sid, {}).get('sid')
                }
                for sid in connected_students.get(room_id, [])
            ]
        }, room=room_id)
        
        emit('room-state', {
            'state': 'live',
            'waitingStudents': 0,
            'connectedStudents': len(connected_students.get(room_id, [])),
            'teacherId': active_rooms[room_id].get('teacher_id'),
            'teacherName': active_rooms[room_id].get('teacher_name')
        }, room=room_id)
        
    except Exception as e:
        debug_print(f"❌ Error in start-meeting: {e}")
        emit('error', {'message': f'Failed to start meeting: {str(e)}'})

@socketio.on('end-meeting')
def handle_end_meeting(data):
    """End the live meeting"""
    try:
        room_id = data.get('room', 'default')
        
        if room_id not in active_rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        active_rooms[room_id]['state'] = 'ended'
        
        debug_print(f"🛑 Meeting ended in room {room_id}")
        
        emit('room-ended', {
            'room': room_id,
            'teacherId': active_rooms[room_id].get('teacher_id', 'unknown'),
            'teacherName': active_rooms[room_id].get('teacher_name', 'Teacher'),
            'message': 'Meeting has ended'
        }, room=room_id)
        
        if room_id in waiting_students:
            del waiting_students[room_id]
        if room_id in connected_students:
            del connected_students[room_id]
        if room_id in active_rooms:
            del active_rooms[room_id]
        
        for user_id in list(student_details.keys()):
            if student_details[user_id].get('room') == room_id:
                del student_details[user_id]
        
    except Exception as e:
        debug_print(f"❌ Error in end-meeting: {e}")
        emit('error', {'message': f'Failed to end meeting: {str(e)}'})

@socketio.on('webrtc-signal')
def handle_webrtc_signal(data):
    """Handle WebRTC signaling for live meetings"""
    try:
        room_id = data.get('room', 'default')
        from_user = data.get('from')
        to_user = data.get('to')
        signal = data.get('signal')
        type = data.get('type', 'signal')
        
        debug_print(f"📡 WebRTC {type} from {from_user} to {to_user} in room {room_id}")
        
        target_sid = None
        if to_user.startswith('teacher_'):
            if room_id in active_rooms:
                target_sid = active_rooms[room_id].get('teacher_sid')
        else:
            if to_user in student_details:
                target_sid = student_details[to_user].get('sid')
        
        if target_sid:
            emit('webrtc-signal', {
                'from': from_user,
                'to': to_user,
                'signal': signal,
                'type': type
            }, room=target_sid)
        else:
            debug_print(f"⚠️ Target user {to_user} not found")
            
    except Exception as e:
        debug_print(f"❌ Error in webrtc-signal: {e}")

@socketio.on('mic-request')
def handle_mic_request(data):
    """Student requests microphone permission"""
    room_id = data.get('room')
    user_id = data.get('userId', 'unknown')
    username = data.get('username', 'Student')
    
    if room_id in active_rooms:
        teacher_sid = active_rooms[room_id].get('teacher_sid')
        if teacher_sid:
            emit('mic-request', {
                'userId': user_id,
                'username': username,
                'socketId': request.sid
            }, room=teacher_sid)

@socketio.on('student-question')
def handle_student_question(data):
    """Student submits a question"""
    room_id = data.get('room')
    user_id = data.get('userId', 'unknown')
    username = data.get('username', 'Student')
    question = data.get('question', '')
    question_id = f"q_{user_id}_{int(datetime.utcnow().timestamp())}"
    
    if room_id in active_rooms:
        teacher_sid = active_rooms[room_id].get('teacher_sid')
        if teacher_sid:
            emit('student-question', {
                'userId': user_id,
                'username': username,
                'question': question,
                'questionId': question_id
            }, room=teacher_sid)

# ============================================
# Flask Routes - Complete Navigation System
# ============================================

# Home & Authentication
@app.route('/')
def index():
    """Home page dashboard"""
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    username = user['username']
    questions = get_questions_for_user(username)
    if questions is None:
        questions = []
    
    # Get user stats
    with app.app_context():
        user_stats = {
            'total_questions': UserQuestions.query.filter_by(username=username).count(),
            'level': user.get('level', 1),
            'points': user.get('points', 0)
        }
    
    return render_template('index.html', 
                         user=user, 
                         questions=questions,
                         stats=user_stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        try:
            with app.app_context():
                user = User.query.filter_by(username=username).first()
                
                if not user:
                    if username and password:
                        # Create temporary user
                        new_user = User(
                            username=username,
                            email=f'{username}@example.com',
                            joined_on=datetime.utcnow()
                        )
                        new_user.set_password(password)
                        db.session.add(new_user)
                        db.session.commit()
                        
                        session['user'] = {
                            'username': username,
                            'email': f'{username}@example.com',
                            'joined_on': datetime.now().strftime('%Y-%m-%d'),
                            'preferred_language': 'English',
                            'last_login': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'level': 1,
                            'points': 0
                        }
                        
                        flash('Logged in successfully!')
                        return redirect(url_for('index'))
                    else:
                        flash('Please enter username and password.')
                        return redirect(url_for('login'))
                
                if user.check_password(password):
                    user.last_login = datetime.utcnow()
                    db.session.commit()

                    session['user'] = {
                        'username': user.username,
                        'email': user.email,
                        'joined_on': user.joined_on.strftime('%Y-%m-%d'),
                        'preferred_language': 'English',
                        'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else '',
                        'level': user.level,
                        'points': user.points
                    }

                    flash('Logged in successfully!')
                    return redirect(url_for('index'))

                else:
                    flash('Invalid username or password.')
                    return redirect(url_for('login'))

        except Exception as e:
            flash(f'Error: {str(e)}')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Signup page"""
    if request.method == 'POST':
        username = request.form.get('username').strip()
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        confirm_password = request.form.get('confirm_password').strip()

        if not username or not password or not email:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))
        
        if password != confirm_password:
            flash('Passwords do not match.')
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
                    'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    'level': 1,
                    'points': 0
                }

                flash('Account created successfully!')
                return redirect(url_for('index'))

        except Exception as e:
            flash(f'Error creating account: {str(e)}')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('login'))

# 👤 Profile Routes
@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    user_data = session.get('user', {})
    username = user_data.get('username')
    
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user:
            user_data['level'] = user.level
            user_data['points'] = user.points
            user_data['bio'] = user.bio
            user_data['profile_picture'] = user.profile_picture
    
    return render_template('profile.html', user=user_data)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Edit profile page"""
    if request.method == 'POST':
        username = session['user']['username']
        bio = request.form.get('bio', '')
        
        with app.app_context():
            user = User.query.filter_by(username=username).first()
            if user:
                user.bio = bio
                
                # Handle profile picture upload
                if 'profile_picture' in request.files:
                    file = request.files['profile_picture']
                    if file and file.filename:
                        filename = secure_filename(f"{username}_{file.filename}")
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(file_path)
                        user.profile_picture = filename
                
                db.session.commit()
                session['user']['bio'] = bio
                
                flash('Profile updated successfully!')
                return redirect(url_for('profile'))
    
    return render_template('edit_profile.html', user=session.get('user'))

# 🎙️ Talk to Tawfiq AI
@app.route('/talk-to-tawfiq')
@login_required
def talk_to_tawfiq():
    """Tawfiq AI chat interface"""
    username = session['user']['username']
    questions = get_questions_for_user(username)
    return render_template('talk_to_tawfiq.html', user=session.get('user'), questions=questions)

@app.route('/api/ask', methods=['POST'])
@login_required
def ask_ai():
    """Tawfiq AI API endpoint"""
    data = request.get_json()
    username = session['user']['username']
    question = data.get('question', '')
    history = data.get('history', [])
    
    if not question:
        return jsonify({'error': 'Question is required'}), 400
    
    try:
        # Check if we have API key for OpenRouter
        if openrouter_api_key:
            headers = {
                "Authorization": f"Bearer {openrouter_api_key}",
                "Content-Type": "application/json"
            }
            
            messages = [
                {
                    "role": "system",
                    "content": "You are Tawfiq AI, an Islamic assistant created by Tella Abdul Afeez Adewale. You provide accurate Islamic knowledge, answer questions about Quran, Hadith, fiqh, history, and daily life issues from an Islamic perspective. Be kind, helpful, and authentic."
                }
            ]
            
            # Add history
            for msg in history[-5:]:  # Last 5 messages for context
                messages.append(msg)
            
            # Add current question
            messages.append({"role": "user", "content": question})
            
            payload = {
                "model": "openai/gpt-3.5-turbo",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 500
            }
            
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content']
                
                # Save to database
                save_question_and_answer(username, question, answer)
                
                return jsonify({
                    'answer': answer,
                    'question': question
                })
            else:
                # Fallback response
                answer = "I apologize, but I'm having trouble accessing advanced features right now. Here's what I can tell you based on my knowledge..."
        else:
            # Local response without API
            answer = "As Tawfiq AI, I'm here to help you with Islamic knowledge. For advanced features, please configure the API key. For now, I recommend consulting authentic Islamic sources like Quran and Hadith for accurate information."
        
        # Save to database even with fallback
        save_question_and_answer(username, question, answer)
        
        return jsonify({
            'answer': answer,
            'question': question
        })
        
    except Exception as e:
        debug_print(f"❌ AI Error: {e}")
        return jsonify({
            'answer': f"I encountered an error: {str(e)}. Please try again later.",
            'question': question
        })

# 🎙️ Live Meeting System
@app.route('/live-meeting')
@login_required
def live_meeting_home():
    """Live meeting landing page"""
    return render_template('live_meeting.html', user=session.get('user'))

@app.route('/live-meeting/create')
@login_required
def create_live_meeting():
    """Create a new live meeting room"""
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('live_meeting_teacher', room_id=room_id))

@app.route('/live-meeting/teacher/<room_id>')
@login_required
def live_meeting_teacher(room_id):
    """Teacher interface for live meeting"""
    return render_template('teacher_live.html', room_id=room_id, user=session.get('user'))

@app.route('/live-meeting/student/<room_id>')
@login_required
def live_meeting_student(room_id):
    """Student interface for live meeting"""
    return render_template('student_live.html', room_id=room_id, user=session.get('user'))

@app.route('/live-meeting/join', methods=['POST'])
@login_required
def join_live_meeting():
    """Join an existing live meeting"""
    room_id = request.form.get('room_id', '').strip()
    if not room_id:
        flash('Please enter a meeting ID')
        return redirect(url_for('live_meeting_home'))
    
    return redirect(url_for('live_meeting_student', room_id=room_id))

# 🕌 Prayer Times
@app.route('/prayer-times')
@login_required
def prayer_times():
    """Prayer times page"""
    # You can integrate with an external API like Aladhan API
    return render_template('prayer_times.html', user=session.get('user'))

@app.route('/api/prayer-times')
def get_prayer_times():
    """API to get prayer times (example using Aladhan API)"""
    try:
        # Get location from request or use default
        city = request.args.get('city', 'Mecca')
        country = request.args.get('country', 'Saudi Arabia')
        
        # Using Aladhan API
        url = f"http://api.aladhan.com/v1/timingsByCity"
        params = {
            'city': city,
            'country': country,
            'method': 2  # Islamic Society of North America
        }
        
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            return jsonify(data['data'])
        else:
            # Return mock data if API fails
            return jsonify({
                'timings': {
                    'Fajr': '5:30',
                    'Dhuhr': '12:30',
                    'Asr': '15:45',
                    'Maghrib': '18:20',
                    'Isha': '19:45'
                },
                'date': {
                    'readable': datetime.now().strftime('%d %B %Y')
                }
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✨ Islamic Motivation
@app.route('/motivation')
@login_required
def motivation():
    """Islamic motivation and quotes"""
    # Get random motivation from loaded data
    motiv_list = islamic_motivation.get('quotes', []) if isinstance(islamic_motivation, dict) else islamic_mototion
    
    if not motiv_list:
        motiv_list = [
            "Verily, with hardship comes ease. (Quran 94:6)",
            "Allah does not burden a soul beyond that it can bear. (Quran 2:286)",
            "The best among you are those who have the best manners and character. (Hadith)",
            "When you forget that you need Allah, He puts you in a situation that causes you to call upon Him. And that's for your own good.",
            "Allah knows what is best for you, so when He says no, trust Him."
        ]
    
    # Get a different quote each day
    day_of_year = datetime.now().timetuple().tm_yday
    quote_index = day_of_year % len(motiv_list)
    daily_quote = motiv_list[quote_index] if isinstance(motiv_list[quote_index], str) else motiv_list[quote_index].get('quote', '')
    
    return render_template('motivation.html', 
                         user=session.get('user'),
                         quote=daily_quote,
                         quotes=motiv_list[:10])

# 📖 Story Time
@app.route('/story-time')
@login_required
def story_time():
    """Islamic stories for kids and adults"""
    stories = stories_data if stories_data else []
    return render_template('story_time.html', 
                         user=session.get('user'),
                         stories=stories[:10])  # Show first 10 stories

@app.route('/story/<int:story_id>')
@login_required
def story_detail(story_id):
    """Individual story page"""
    stories = stories_data if stories_data else []
    if 0 <= story_id < len(stories):
        story = stories[story_id]
    else:
        story = {
            'title': 'Story Not Found',
            'content': 'The requested story could not be found.',
            'moral': '',
            'category': 'General'
        }
    
    return render_template('story_detail.html', 
                         user=session.get('user'),
                         story=story,
                         story_id=story_id)

# 📱 Reels (Islamic Videos)
@app.route('/reels')
@login_required
def reels():
    """Islamic educational videos"""
    # Hardcoded reel data - you can replace with database or API
    reels_data = [
        {
            'title': 'The Story of Prophet Muhammad (ﷺ) by Mufti Menk',
            'youtube_id': 'DdWxCVYAOCk',
            'description': 'A brief overview of the life and teachings of Prophet Muhammad (S.A.W).',
            'duration': '15:30',
            'category': 'Seerah'
        },
        {
            'title': 'How to Perform Wudu Correctly',
            'youtube_id': 'R06y6XF7mLk',
            'description': 'Step-by-step guide on performing ablution (wudu).',
            'duration': '8:45',
            'category': 'Fiqh'
        },
        {
            'title': '10 Duas Every Muslim Should Know',
            'youtube_id': 'CBhCc_Fxa4g',
            'description': 'Important daily duas from Quran and Sunnah.',
            'duration': '12:20',
            'category': 'Dua'
        },
        {
            'title': 'The Miracle of Quran',
            'youtube_id': 'W7iR5B1MSWc',
            'description': 'Scientific miracles in the Holy Quran.',
            'duration': '20:15',
            'category': 'Quran'
        },
        {
            'title': 'Patience in Islam - Story of Prophet Ayyub',
            'youtube_id': 'hj8eYLUViQI',
            'description': 'Lessons in patience from the life of Prophet Ayyub (AS).',
            'duration': '18:30',
            'category': 'Stories'
        }
    ]
    
    return render_template('reels.html', 
                         user=session.get('user'),
                         reels=reels_data)

# 🎮 Play Game (Islamic Trivia)
@app.route('/play-game')
@login_required
def play_game():
    """Game selection page"""
    # Get user's game stats
    username = session['user']['username']
    with app.app_context():
        trivia_scores = GameScore.query.filter_by(
            username=username, 
            game_type='trivia'
        ).order_by(GameScore.score.desc()).limit(5).all()
        
        high_score = max([score.score for score in trivia_scores]) if trivia_scores else 0
    
    return render_template('play_game.html', 
                         user=session.get('user'),
                         high_score=high_score,
                         trivia_scores=trivia_scores)

@app.route('/game/trivia')
@login_required
def trivia_game():
    """Islamic trivia game"""
    # Questions database
    questions = basic_knowledge_data.get('questions', []) if isinstance(basic_knowledge_data, dict) else basic_knowledge_data
    
    if not questions:
        # Fallback questions
        questions = [
            {
                'question': 'What is the first month of the Islamic calendar?',
                'options': ['Ramadan', 'Muharram', 'Shawwal', 'Dhul-Hijjah'],
                'answer': 'Muharram',
                'category': 'Calendar'
            },
            {
                'question': 'How many pillars of Islam are there?',
                'options': ['3', '4', '5', '6'],
                'answer': '5',
                'category': 'Aqeedah'
            },
            {
                'question': 'Which Surah is called the "Heart of the Quran"?',
                'options': ['Al-Fatiha', 'Yasin', 'Al-Baqarah', 'Al-Ikhlas'],
                'answer': 'Yasin',
                'category': 'Quran'
            }
        ]
    
    return render_template('trivia_game.html', 
                         user=session.get('user'),
                         questions=questions[:10])  # First 10 questions

@app.route('/api/game/submit-score', methods=['POST'])
@login_required
def submit_game_score():
    """Submit game score"""
    data = request.get_json()
    username = session['user']['username']
    game_type = data.get('game_type', 'trivia')
    score = data.get('score', 0)
    level = data.get('level', 1)
    
    try:
        with app.app_context():
            game_score = GameScore(
                username=username,
                game_type=game_type,
                score=score,
                level=level
            )
            db.session.add(game_score)
            
            # Update user points
            user = User.query.filter_by(username=username).first()
            if user:
                user.points += score // 10  # Convert score to points
                if score > 50 and user.level < 2:
                    user.level = 2
                elif score > 100 and user.level < 3:
                    user.level = 3
            
            db.session.commit()
            
            # Update session
            session['user']['points'] = user.points if user else 0
            session['user']['level'] = user.level if user else 1
            
            return jsonify({
                'success': True,
                'message': 'Score saved successfully',
                'new_points': user.points if user else 0,
                'new_level': user.level if user else 1
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ⚙️ Settings
@app.route('/settings')
@login_required
def settings():
    """User settings page"""
    user_data = session.get('user', {})
    return render_template('settings.html', user=user_data)

@app.route('/settings/update', methods=['POST'])
@login_required
def update_settings():
    """Update user settings"""
    username = session['user']['username']
    language = request.form.get('language', 'English')
    theme = request.form.get('theme', 'light')
    notifications = request.form.get('notifications', 'off') == 'on'
    
    # Update session
    session['user']['preferred_language'] = language
    session['user']['theme'] = theme
    session['user']['notifications'] = notifications
    
    flash('Settings updated successfully!')
    return redirect(url_for('settings'))

# 🔒 Privacy & Policy
@app.route('/privacy')
def privacy():
    """Privacy policy page"""
    return render_template('privacy.html', user=session.get('user'))

# ℹ️ About Tawfiq AI
@app.route('/about')
def about():
    """About page"""
    return render_template('about.html', user=session.get('user'))

# 📢 Feedback / Report
@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    """Feedback submission page"""
    if request.method == 'POST':
        username = session['user']['username']
        email = session['user'].get('email', '')
        feedback_type = request.form.get('type', 'feedback')
        message = request.form.get('message', '')
        
        if not message:
            flash('Please enter your feedback message')
            return redirect(url_for('feedback'))
        
        try:
            with app.app_context():
                feedback_entry = Feedback(
                    username=username,
                    email=email,
                    type=feedback_type,
                    message=message
                )
                db.session.add(feedback_entry)
                db.session.commit()
            
            flash('Thank you for your feedback! We appreciate your input.')
            return redirect(url_for('feedback'))
        except Exception as e:
            flash(f'Error submitting feedback: {str(e)}')
            return redirect(url_for('feedback'))
    
    return render_template('feedback.html', user=session.get('user'))

# ============================================
# Additional Features & API Endpoints
# ============================================

# Daily Dua
@app.route('/daily-dua')
@login_required
def daily_dua():
    """Get daily Dua"""
    duas = daily_duas.get('duas', []) if isinstance(daily_duas, dict) else daily_duas
    
    if not duas:
        duas = [
            {
                'arabic': 'رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً وَفِي الْآخِرَةِ حَسَنَةً وَقِنَا عَذَابَ النَّارِ',
                'english': 'Our Lord, give us in this world [that which is] good and in the Hereafter [that which is] good and protect us from the punishment of the Fire.',
                'reference': 'Quran 2:201',
                'category': 'General'
            }
        ]
    
    # Get different Dua each day
    day_of_year = datetime.now().timetuple().tm_yday
    dua_index = day_of_year % len(duas)
    daily = duas[dua_index]
    
    return render_template('daily_dua.html', 
                         user=session.get('user'),
                         dua=daily)

# Hadith Search
@app.route('/hadith-search')
@login_required
def hadith_search_page():
    """Hadith search page"""
    return render_template('hadith_search.html', user=session.get('user'))

@app.route('/api/hadith/search', methods=['POST'])
def search_hadith():
    """Search hadith API"""
    data = request.get_json()
    query = data.get('query', '').lower().strip()
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    results = []
    
    # Search in loaded hadith data
    if hadith_data and isinstance(hadith_data, dict):
        volumes = hadith_data.get('volumes', [])
        for volume in volumes:
            books = volume.get('books', [])
            for book in books:
                hadiths = book.get('hadiths', [])
                for hadith in hadiths:
                    text = hadith.get('text', '').lower()
                    if query in text:
                        results.append({
                            'volume': volume.get('volume_number', ''),
                            'book': book.get('book_name', ''),
                            'text': hadith.get('text', ''),
                            'narrator': hadith.get('by', ''),
                            'reference': f"Volume {volume.get('volume_number', '')}, Book {book.get('book_number', '')}"
                        })
                    
                    if len(results) >= 10:  # Limit results
                        break
                if len(results) >= 10:
                    break
            if len(results) >= 10:
                break
    
    return jsonify({'results': results})

# Quran Surah
@app.route('/quran')
@login_required
def quran():
    """Quran reading page"""
    return render_template('quran.html', user=session.get('user'))

# Reminders
@app.route('/reminders')
@login_required
def reminders():
    """Islamic reminders page"""
    reminders_list = reminders_data if reminders_data else []
    return render_template('reminders.html', 
                         user=session.get('user'),
                         reminders=reminders_list)

# Memorize Quran
@app.route('/memorize-quran')
@login_required
def memorize_quran():
    """Quran memorization helper"""
    return render_template('memorize_quran.html', user=session.get('user'))

# ============================================
# Static Files & Debug Routes
# ============================================

@app.route('/static/<path:filename>')
def static_files(filename):
    """Serve static files"""
    return send_from_directory('static', filename)

@app.route('/test-connection')
def test_connection():
    """Test WebSocket connection"""
    return render_template('test_connection.html')

@app.route('/debug/rooms')
def debug_rooms():
    """Debug room information"""
    if not DEBUG_MODE:
        return "Debug mode is disabled"
    
    debug_info = {
        'rooms': rooms,
        'participants': participants,
        'active_rooms': active_rooms,
        'total_rooms': len(rooms),
        'total_participants': len(participants),
        'total_active_rooms': len(active_rooms)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Error Handlers
# ============================================

@app.errorhandler(404)
def page_not_found(e):
    """404 error handler"""
    return render_template('404.html', user=session.get('user')), 404

@app.errorhandler(500)
def internal_server_error(e):
    """500 error handler"""
    return render_template('500.html', user=session.get('user')), 500

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 TAWFIQ AI - COMPLETE ISLAMIC PLATFORM")
    print(f"{'='*60}")
    print("👤 Profile Management")
    print("🎙️ Talk to Tawfiq AI")
    print("🎙️ Live Meeting System (WebRTC)")
    print("🕌 Prayer Times")
    print("✨ Islamic Motivation")
    print("📖 Story Time")
    print("📱 Islamic Reels/Videos")
    print("🎮 Islamic Trivia Games")
    print("⚙️ User Settings")
    print("🔒 Privacy & Policy")
    print("ℹ️ About Tawfiq AI")
    print("📢 Feedback System")
    print(f"{'='*60}")
    print("\n📡 Server starting...")
    print("🌐 Access at: http://localhost:5000")
    print("🔌 Test WebSocket: http://localhost:5000/test-connection")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, 
                host='0.0.0.0', 
                port=port, 
                debug=DEBUG_MODE, 
                allow_unsafe_werkzeug=True)
