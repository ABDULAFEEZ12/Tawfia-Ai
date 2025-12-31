import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
import os
import json
from datetime import datetime
from flask import Flask, render_template, session, redirect, url_for, request, flash
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
import uuid

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Debug mode - set to False in production
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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

# ============================================
# In-Memory Storage
# ============================================
rooms = {}           # room_id -> room data
participants = {}    # socket_id -> participant info
room_authority = {}  # room_id -> authority state

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

# ============================================
# Socket.IO Event Handlers
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid}")

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
                emit('teacher-disconnected', room=room_id, skip_sid=sid)
            
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
    """Join room and get all existing participants"""
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
        
        # Check if student is trying to join without teacher
        if role == 'student' and not room['teacher_sid']:
            emit('error', {'message': 'Teacher not in room. Please wait.'})
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
            'teacher_sid': room['teacher_sid']
        })
        
        # Notify all other participants about new joiner
        emit('new-participant', {
            'sid': sid,
            'username': username,
            'role': role
        }, room=room_id, skip_sid=sid)
        
        # Send authority state if student
        if role == 'student':
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
# WebRTC Signaling - Full Mesh Support
# ============================================
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant"""
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
        
        # Relay offer to target
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
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        debug_print(f"📨 {request.sid[:8]} → answer → {target_sid[:8]}")
        
        # Relay answer to target
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
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        # Relay candidate to target
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying ICE candidate: {e}")

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

@socketio.on('teacher-disable-cameras')
def handle_teacher_disable_cameras(data):
    """Teacher disables all student cameras"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['cameras_disabled'] = True
        
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('cameras-disabled', {'disabled': True}, room=sid)
        
        debug_print(f"📷 Teacher disabled cameras in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-disable-cameras: {e}")

@socketio.on('teacher-enable-cameras')
def handle_teacher_enable_cameras(data):
    """Teacher enables all student cameras"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['cameras_disabled'] = False
        
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('cameras-disabled', {'disabled': False}, room=sid)
        
        debug_print(f"📸 Teacher enabled cameras in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-enable-cameras: {e}")

@socketio.on('student-request-mic')
def handle_student_mic_request(data):
    """Student requests microphone permission"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        # Check if student is in room
        if student_sid not in room['participants']:
            return
        
        student_info = room['participants'][student_sid]
        authority = get_room_authority(room_id)
        
        # Check if room is muted
        if authority['muted_all']:
            emit('error', {'message': 'Room is muted by teacher'}, room=student_sid)
            return
        
        # Add to pending requests
        authority['mic_requests'][student_sid] = student_info['username']
        
        # Notify teacher
        if room['teacher_sid']:
            emit('mic-request-received', {
                'student_sid': student_sid,
                'username': student_info['username'],
                'count': len(authority['mic_requests'])
            }, room=room['teacher_sid'])
        
        debug_print(f"🎤 {student_info['username']} requested mic in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in student-request-mic: {e}")

@socketio.on('teacher-approve-mic')
def handle_teacher_approve_mic(data):
    """Teacher approves student's microphone request"""
    try:
        room_id = data.get('room')
        student_sid = data.get('student_sid')
        
        if not room_id or not student_sid:
            return
        
        room = rooms.get(room_id)
        if not room:
            return
        
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        
        if student_sid in authority['mic_requests']:
            # Remove from pending requests
            del authority['mic_requests'][student_sid]
            
            # Notify the student
            emit('mic-approved', {'approved': True}, room=student_sid)
            
            # Update teacher's request count
            emit('mic-requests-update', {
                'count': len(authority['mic_requests'])
            }, room=teacher_sid)
            
            debug_print(f"✅ Teacher approved mic for student {student_sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-approve-mic: {e}")

@socketio.on('student-struggle-signal')
def handle_struggle_signal(data):
    """Student sends private struggle signal to teacher"""
    try:
        room_id = data.get('room')
        signal = data.get('signal')  # 'confused', 'too_fast', 'got_it'
        
        if not room_id or not signal:
            return
        
        room = rooms.get(room_id)
        if not room:
            return
        
        student_sid = request.sid
        
        # Check if student is in room
        if student_sid not in room['participants']:
            return
        
        student_info = room['participants'][student_sid]
        
        # Forward to teacher only
        if room['teacher_sid']:
            emit('student-struggling', {
                'student_sid': student_sid,
                'username': student_info['username'],
                'signal': signal
            }, room=room['teacher_sid'])
        
        debug_print(f"💡 {student_info['username']} sent {signal} signal in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in student-struggle-signal: {e}")

@socketio.on('teacher-toggle-questions')
def handle_teacher_toggle_questions(data):
    """Teacher enables/disables questions"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['questions_enabled'] = not authority['questions_enabled']
        
        # Notify all participants except teacher
        for sid in room['participants']:
            if sid != teacher_sid:
                emit('questions-toggled', {
                    'enabled': authority['questions_enabled']
                }, room=sid)
        
        debug_print(f"❓ Teacher {'enabled' if authority['questions_enabled'] else 'disabled'} questions in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-toggle-questions: {e}")

# ============================================
# Raise Hand & Feedback System
# ============================================

@socketio.on('raise-hand')
def handle_raise_hand(data):
    """Student raises hand"""
    try:
        room_id = data.get('room')
        student_name = data.get('studentName', 'Student')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        # Check if student is in room
        if student_sid not in room['participants']:
            return
        
        # Store student name if not already stored
        room['participants'][student_sid]['username'] = student_name
        
        # Notify teacher only
        if room['teacher_sid']:
            emit('student-raised-hand', {
                'sid': student_sid,
                'studentName': student_name,
                'timestamp': datetime.utcnow().isoformat()
            }, room=room['teacher_sid'])
        
        debug_print(f"✋ {student_name} raised hand in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in raise-hand: {e}")

@socketio.on('lower-hand')
def handle_lower_hand(data):
    """Student lowers hand"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        # Notify teacher only
        if room['teacher_sid']:
            emit('student-lowered-hand', {
                'sid': student_sid
            }, room=room['teacher_sid'])
        
        debug_print(f"✋ Student lowered hand in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in lower-hand: {e}")

@socketio.on('student-feedback')
def handle_student_feedback(data):
    """Student sends quick feedback (buttons or text)"""
    try:
        room_id = data.get('room')
        feedback_type = data.get('type')  # 'confused', 'fast', 'gotit', 'text'
        student_name = data.get('studentName', 'Student')
        text = data.get('text', '')
        
        if not room_id or not feedback_type:
            return
        
        room = rooms.get(room_id)
        if not room:
            return
        
        student_sid = request.sid
        
        # Forward to teacher only
        if room['teacher_sid']:
            feedback_data = {
                'sid': student_sid,
                'studentName': student_name,
                'type': feedback_type,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            if feedback_type == 'text' and text:
                feedback_data['text'] = text
            
            emit('student-feedback', feedback_data, room=room['teacher_sid'])
        
        debug_print(f"💬 {student_name} sent feedback: {feedback_type} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in student-feedback: {e}")

@socketio.on('teacher-acknowledge-hand')
def handle_teacher_acknowledge_hand(data):
    """Teacher acknowledges a raised hand"""
    try:
        room_id = data.get('room')
        student_sid = data.get('student_sid')
        
        if not room_id or not student_sid:
            return
        
        room = rooms.get(room_id)
        if not room:
            return
        
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        teacher_name = room['participants'][teacher_sid]['username']
        
        # Notify the student
        emit('hand-acknowledged', {
            'teacher': teacher_name,
            'message': f'{teacher_name} acknowledged your hand'
        }, room=student_sid)
        
        debug_print(f"✅ Teacher acknowledged hand for student {student_sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-acknowledge-hand: {e}")

# ============================================
# Control Events
# ============================================
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
        
        # Get all student SIDs
        student_sids = []
        for sid, info in room['participants'].items():
            if info['role'] == 'student':
                student_sids.append(sid)
        
        emit('broadcast-ready', {
            'student_sids': student_sids,
            'student_count': len(student_sids),
            'room': room_id
        })
        
        for student_sid in student_sids:
            emit('teacher-ready', {
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
# Flask Routes
# ============================================
@app.route('/')
def index():
    return render_template('index.html')

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
# Live Meeting Routes
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
# Run Server
# ============================================

    port = int(os.environ.get("PORT", 8000))
