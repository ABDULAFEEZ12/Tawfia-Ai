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
    request, flash, jsonify, send_from_directory
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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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
    try:
        db.create_all()
        debug_print("✅ Database tables created successfully")
    except Exception as e:
        debug_print(f"⚠️ Database creation error: {e}")

# ============================================
# In-Memory Storage for Live Meetings
# ============================================
rooms = {}           # room_id -> room data
participants = {}    # socket_id -> participant info
room_authority = {}  # room_id -> authority state
active_rooms = {}    # For live meeting system
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

# ============================================
# Socket.IO Event Handlers - Combined
# ============================================

# Main connection handler
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    # For WebRTC meetings
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid}")

# Main disconnect handler
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
                
                debug_print(f"❌ {participant_info['username']} left room {room_id}")
            
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
        
        debug_print(f"📨 {request.sid[:8]} → offer → {target_sid[:8]}")
        
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
        
        debug_print(f"📨 {request.sid[:8]} → answer → {target_sid[:8]}")
        
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
        
        debug_print(f"📨 {request.sid[:8]} → ICE → {target_sid[:8]}")
        
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying ICE candidate: {e}")

# Full Mesh Initiation
@socketio.on('request-full-mesh')
def handle_request_full_mesh(data):
    """Initiate full mesh connections between all participants"""
    try:
        room_id = data.get('room')
        sid = request.sid
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        
        if sid not in room['participants']:
            return
        
        other_participants = []
        for other_sid, info in room['participants'].items():
            if other_sid != sid:
                other_participants.append({
                    'sid': other_sid,
                    'username': info['username'],
                    'role': info['role']
                })
        
        emit('initiate-mesh-connections', {
            'peers': other_participants,
            'room': room_id
        }, room=sid)
        
        debug_print(f"🔗 Initiating full mesh for {sid[:8]} with {len(other_participants)} peers")
        
    except Exception as e:
        debug_print(f"❌ Error in request-full-mesh: {e}")

# Teacher Authority System
@socketio.on('teacher-mute-all')
def handle_teacher_mute_all(data):
    """Teacher mutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['muted_all'] = True
        
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

# Control Events
@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students"""
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
        
        student_sids = []
        student_info = []
        for sid, info in room['participants'].items():
            if info['role'] == 'student':
                student_sids.append(sid)
                student_info.append({
                    'sid': sid,
                    'username': info['username']
                })
        
        emit('broadcast-ready', {
            'student_sids': student_sids,
            'student_info': student_info,
            'student_count': len(student_sids),
            'room': room_id
        }, room=teacher_sid)
        
        for student_sid in student_sids:
            peers_to_connect = []
            for other_sid in room['participants']:
                if other_sid != student_sid:
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

# ============================================
# Flask Routes - Combined
# ============================================

# Authentication Routes
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
                
                if not user:
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

@app.route('/logout')
def logout():
    session.clear()
    return """
    <h2>You have been logged out</h2>
    <a href="/login">Login Again</a> | <a href="/signup">Create Account</a>
    """

# WebRTC Meeting Routes
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

# Live Meeting Routes
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

# Tawfiq AI Routes
@app.route('/talk-to-tawfiq')
def talk_to_tawfiq():
    return render_template('talk_to_tawfiq.html')

@app.route('/ask', methods=['POST'])
def ask():
    # Implement Tawfiq AI logic here
    data = request.get_json()
    username = session.get('user', {}).get('username')
    history = data.get('history')

    if not username:
        return jsonify({'error': 'You must be logged in to chat with Tawfiq AI.'}), 401
    if not history:
        return jsonify({'error': 'Chat history is required.'}), 400

    # Simplified response for now
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Assalamu alaikum! I'm Tawfiq AI, your Islamic learning assistant. How can I help you today?"
            }
        }]
    }
    
    return jsonify(response)

# Additional Routes from Tawfiq AI
@app.route('/profile')
def profile():
    user = session.get('user', {})
    return render_template('profile.html',
                           username=user.get('username', 'Guest'),
                           email=user.get('email', 'not_set@example.com'),
                           joined_on=user.get('joined_on', 'Unknown'),
                           preferred_language=user.get('preferred_language', 'English'),
                           last_login=user.get('last_login', 'N/A'))

@app.route('/my-questions')
def my_questions():
    try:
        username = session['user']['username']
        with app.app_context():
            questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
        return render_template('my_questions.html', questions=questions)
    except Exception as e:
        flash(f'Error loading questions: {str(e)}')
        return render_template('my_questions.html', questions=[])

@app.route('/trivia')
def trivia():
    # Implement trivia logic here
    return render_template('trivia.html')

@app.route('/reels')
def reels():
    reels_data = [
        {
            'title': 'The Story of Prophet Muhammad (ﷺ)  by Mufti Menk',
            'youtube_id': 'DdWxCVYAOCk',
            'description': 'A brief overview of the life and teachings of Prophet Muhammad (S.A.W).'
        },
        # Add more reels as needed
    ]
    return render_template('reels.html', reels=reels_data)

# Debug Routes
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
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 TAWFIQ AI + LIVE MEETING SYSTEM - MERGED")
    print("🌟 Islamic Learning Platform with Live WebRTC Meetings")
    print("👨‍🏫 Teacher-Student Interactive System")
    print(f"{'='*60}")
    print("✅ WebRTC Live Meetings")
    print("✅ Tawfiq AI Assistant")
    print("✅ User Authentication")
    print("✅ Database Integration")
    print(f"{'='=60}")
    print("\n📡 Connection test: http://localhost:5000/test-connection")
    print("👨‍🏫 Teacher test: http://localhost:5000/teacher")
    print("👨‍🎓 Student test: http://localhost:5000/live_meeting")
    print("🤖 Tawfiq AI: http://localhost:5000/talk-to-tawfiq")
    print(f"{'='=60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE, allow_unsafe_werkzeug=True)
