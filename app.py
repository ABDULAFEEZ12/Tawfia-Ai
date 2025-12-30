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
    print("✅ Database tables created")

# ============================================
# In-Memory Storage
# ============================================
rooms = {}
sessions = {}

# Room authority state (NELAVISTA Phase 1)
room_authority = {}

# ============================================
# Helper Functions
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'teacher_sid': None,
            'students': {},
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def get_room_authority(room_id):
    """Get or create authority state for a room"""
    if room_id not in room_authority:
        room_authority[room_id] = {
            'teacher_sid': None,
            'muted_all': False,
            'cameras_disabled': False,
            'mic_requests': {},  # {student_sid: username}
            'questions_enabled': True,
            'question_visibility': 'public'
        }
    return room_authority[room_id]

def cleanup_room(room_id):
    """Remove empty rooms"""
    if room_id in rooms:
        room = rooms[room_id]
        if not room['teacher_sid'] and not room['students']:
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
    sessions[sid] = {'room': None, 'role': None, 'username': None}
    print(f"✅ Client connected: {sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sessions:
        session_data = sessions[sid]
        room_id = session_data.get('room')
        
        if room_id:
            # Clean up authority state
            if room_id in room_authority:
                authority_state = room_authority[room_id]
                
                if sid == authority_state['teacher_sid']:
                    # Teacher disconnected, reset authority state
                    authority_state['teacher_sid'] = None
                    authority_state['muted_all'] = False
                    authority_state['cameras_disabled'] = False
                    authority_state['mic_requests'] = {}
                
                # Remove student from mic requests
                if sid in authority_state['mic_requests']:
                    del authority_state['mic_requests'][sid]
                    
                    # Notify teacher if still connected
                    if authority_state['teacher_sid']:
                        emit('mic-requests-update', {
                            'count': len(authority_state['mic_requests'])
                        }, room=authority_state['teacher_sid'])
            
            # Clean up room
            if room_id in rooms:
                room = rooms[room_id]
                
                if session_data['role'] == 'teacher':
                    room['teacher_sid'] = None
                    with app.app_context():
                        room_db = Room.query.get(room_id)
                        if room_db:
                            room_db.teacher_id = None
                            db.session.commit()
                    for student_sid in room['students']:
                        emit('teacher-disconnected', room=student_sid)
                elif session_data['role'] == 'student':
                    if sid in room['students']:
                        del room['students'][sid]
                        if room['teacher_sid']:
                            emit('student-left', {'sid': sid}, room=room['teacher_sid'])
                
                cleanup_room(room_id)
        
        del sessions[sid]
    print(f"❌ Client disconnected: {sid}")

@socketio.on('join-room')
def handle_join_room(data):
    """One join path for both teacher and student"""
    try:
        sid = request.sid
        room_id = data.get('room')
        role = data.get('role', 'student')
        username = data.get('username', 'User' if role == 'teacher' else 'Student')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👤 {username} ({role}) joining room: {room_id}")
        
        room = get_or_create_room(room_id)
        authority_state = get_room_authority(room_id)
        
        if role == 'teacher':
            if room['teacher_sid']:
                emit('error', {'message': 'Room already has a teacher'})
                return
            
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
            
            emit('room-joined', {
                'role': 'teacher',
                'room': room_id,
                'message': 'You are now the teacher',
                'sid': sid
            })
            
            print(f"✅ Teacher joined room: {room_id}")
            
        else:
            if not room['teacher_sid']:
                emit('error', {'message': 'Teacher not in room. Please wait.'})
                return
            
            room['students'][sid] = username
            
            # Send current room state to student
            emit('room-joined', {
                'role': 'student',
                'room': room_id,
                'message': 'Joined classroom successfully',
                'sid': sid,
                'teacher_sid': room['teacher_sid'],
                'teacher_name': 'Teacher'  # Default name
            })
            
            # Send initial authority state
            emit('room-state', {
                'muted_all': authority_state['muted_all'],
                'cameras_disabled': authority_state['cameras_disabled'],
                'questions_enabled': authority_state['questions_enabled'],
                'question_visibility': authority_state['question_visibility']
            }, room=sid)
            
            emit('student-joined', {
                'sid': sid,
                'username': username
            }, room=room['teacher_sid'])
            
            print(f"✅ Student joined room: {room_id}")
        
        sessions[sid]['room'] = room_id
        sessions[sid]['role'] = role
        sessions[sid]['username'] = username
        
        join_room(room_id)
        
    except Exception as e:
        print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# ============================================
# NELAVISTA Phase 1: Teacher Authority Events
# ============================================
@socketio.on('teacher-mute-all')
def handle_teacher_mute_all(data):
    """Teacher mutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        authority_state['muted_all'] = True
        
        # Broadcast to all students EXCEPT teacher
        emit('room-muted', {'muted': True}, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'mute-all',
            'status': 'success'
        }, room=teacher_sid)
        
        print(f"🔇 Teacher muted all students in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-mute-all: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-unmute-all')
def handle_teacher_unmute_all(data):
    """Teacher unmutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        authority_state['muted_all'] = False
        
        # Broadcast to all students EXCEPT teacher
        emit('room-muted', {'muted': False}, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'unmute-all',
            'status': 'success'
        }, room=teacher_sid)
        
        print(f"🔊 Teacher unmuted all students in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-unmute-all: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-disable-cameras')
def handle_teacher_disable_cameras(data):
    """Teacher disables all student cameras"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        authority_state['cameras_disabled'] = True
        
        # Broadcast to all students EXCEPT teacher
        emit('cameras-disabled', {'disabled': True}, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'disable-cameras',
            'status': 'success'
        }, room=teacher_sid)
        
        print(f"📷 Teacher disabled all cameras in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-disable-cameras: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-enable-cameras')
def handle_teacher_enable_cameras(data):
    """Teacher enables all student cameras"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        authority_state['cameras_disabled'] = False
        
        # Broadcast to all students EXCEPT teacher
        emit('cameras-disabled', {'disabled': False}, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'enable-cameras',
            'status': 'success'
        }, room=teacher_sid)
        
        print(f"📸 Teacher enabled all cameras in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-enable-cameras: {e}")
        emit('error', {'message': str(e)})

@socketio.on('student-request-mic')
def handle_student_mic_request(data):
    """Student requests microphone permission"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        student_sid = request.sid
        
        # Check if room is muted
        if authority_state['muted_all']:
            emit('error', {'message': 'Room is muted by teacher'}, room=student_sid)
            return
        
        # Check if student is in room
        if room_id not in rooms or student_sid not in rooms[room_id]['students']:
            emit('error', {'message': 'Student not in room'}, room=student_sid)
            return
        
        student_username = rooms[room_id]['students'][student_sid]
        
        # Add to pending requests
        authority_state['mic_requests'][student_sid] = student_username
        
        # Notify teacher if connected
        if authority_state['teacher_sid']:
            emit('mic-request-received', {
                'student_sid': student_sid,
                'username': student_username,
                'count': len(authority_state['mic_requests'])
            }, room=authority_state['teacher_sid'])
        
        print(f"🎤 Student {student_username} requested mic in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in student-request-mic: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-approve-mic')
def handle_teacher_approve_mic(data):
    """Teacher approves student's microphone request"""
    try:
        room_id = data.get('room')
        student_sid = data.get('student_sid')
        
        if not room_id or not student_sid:
            emit('error', {'message': 'Room ID and student SID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        if student_sid in authority_state['mic_requests']:
            # Remove from pending requests
            del authority_state['mic_requests'][student_sid]
            
            # Notify the specific student
            emit('mic-approved', {'approved': True}, room=student_sid)
            
            # Update teacher's request count
            emit('mic-requests-update', {
                'count': len(authority_state['mic_requests'])
            }, room=teacher_sid)
            
            print(f"✅ Teacher approved mic for student {student_sid} in room: {room_id}")
        else:
            emit('error', {'message': 'No pending request for this student'}, room=teacher_sid)
        
    except Exception as e:
        print(f"❌ Error in teacher-approve-mic: {e}")
        emit('error', {'message': str(e)})

@socketio.on('student-struggle-signal')
def handle_struggle_signal(data):
    """Student sends private struggle signal to teacher"""
    try:
        room_id = data.get('room')
        signal = data.get('signal')  # 'confused', 'too_fast', 'got_it'
        
        if not room_id or not signal:
            emit('error', {'message': 'Room ID and signal required'})
            return
        
        authority_state = get_room_authority(room_id)
        student_sid = request.sid
        
        # Check if student is in room
        if room_id not in rooms or student_sid not in rooms[room_id]['students']:
            emit('error', {'message': 'Student not in room'}, room=student_sid)
            return
        
        student_username = rooms[room_id]['students'][student_sid]
        
        # Forward to teacher only
        if authority_state['teacher_sid']:
            emit('student-struggling', {
                'student_sid': student_sid,
                'username': student_username,
                'signal': signal
            }, room=authority_state['teacher_sid'])
        
        print(f"💡 Student {student_username} sent {signal} signal in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in student-struggle-signal: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-toggle-questions')
def handle_teacher_toggle_questions(data):
    """Teacher enables/disables questions"""
    try:
        room_id = data.get('room')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        # Toggle question status
        authority_state['questions_enabled'] = not authority_state['questions_enabled']
        
        # Broadcast to all students
        emit('questions-toggled', {
            'enabled': authority_state['questions_enabled']
        }, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'toggle-questions',
            'status': 'success',
            'enabled': authority_state['questions_enabled']
        }, room=teacher_sid)
        
        print(f"❓ Teacher {'enabled' if authority_state['questions_enabled'] else 'disabled'} questions in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-toggle-questions: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-set-question-visibility')
def handle_teacher_set_question_visibility(data):
    """Teacher sets question visibility mode"""
    try:
        room_id = data.get('room')
        visibility = data.get('visibility')  # 'public' or 'anonymous'
        
        if not room_id or visibility not in ['public', 'anonymous']:
            emit('error', {'message': 'Valid room ID and visibility required'})
            return
        
        authority_state = get_room_authority(room_id)
        teacher_sid = request.sid
        
        if teacher_sid != authority_state['teacher_sid']:
            emit('error', {'message': 'Not authorized'}, room=teacher_sid)
            return
        
        authority_state['question_visibility'] = visibility
        
        # Broadcast to all students
        emit('question-visibility-changed', {
            'visibility': visibility
        }, room=room_id, skip_sid=teacher_sid)
        
        # Confirm to teacher
        emit('command-executed', {
            'command': 'set-question-visibility',
            'status': 'success',
            'visibility': visibility
        }, room=teacher_sid)
        
        print(f"👁️ Teacher set question visibility to {visibility} in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-set-question-visibility: {e}")
        emit('error', {'message': str(e)})

# ============================================
# WebRTC Signaling (SIMPLE RELAY ONLY)
# ============================================
@socketio.on('rtc-offer')
def handle_rtc_offer(data):
    """Teacher sends offer to specific student"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        offer = data.get('offer')
        
        if not all([room_id, target_sid, offer]):
            emit('error', {'message': 'Missing offer data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can send offers'})
            return
        
        if target_sid not in room['students']:
            emit('error', {'message': 'Student not found in room'})
            return
        
        print(f"🎥 Teacher sending offer to student {target_sid}")
        
        emit('rtc-offer', {
            'offer': offer,
            'from_teacher': teacher_sid,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error in rtc-offer: {e}")
        emit('error', {'message': str(e)})

@socketio.on('rtc-answer')
def handle_rtc_answer(data):
    """Student sends answer to teacher"""
    try:
        room_id = data.get('room')
        answer = data.get('answer')
        
        if not all([room_id, answer]):
            emit('error', {'message': 'Missing answer data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        if student_sid not in room['students']:
            emit('error', {'message': 'Not authorized'})
            return
        
        if not room['teacher_sid']:
            emit('error', {'message': 'Teacher not available'})
            return
        
        print(f"🎥 Student {student_sid} sending answer to teacher")
        
        emit('rtc-answer', {
            'answer': answer,
            'from_student': student_sid,
            'room': room_id
        }, room=room['teacher_sid'])
        
    except Exception as e:
        print(f"❌ Error in rtc-answer: {e}")
        emit('error', {'message': str(e)})

@socketio.on('rtc-ice-candidate')
def handle_rtc_ice_candidate(data):
    """Exchange ICE candidates (SIMPLE RELAY)"""
    try:
        room_id = data.get('room')
        candidate = data.get('candidate')
        target_sid = data.get('target_sid')
        
        if not all([room_id, candidate, target_sid]):
            return  # Silently ignore, some candidates may be incomplete
        
        if room_id not in rooms:
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        is_teacher = (sender_sid == room['teacher_sid'])
        is_student = (sender_sid in room['students'])
        
        if not (is_teacher or is_student):
            return
        
        is_target_teacher = (target_sid == room['teacher_sid'])
        is_target_student = (target_sid in room['students'])
        
        if not (is_target_teacher or is_target_student):
            return
        
        # RELAY THE CANDIDATE WITHOUT MODIFICATION
        emit('rtc-ice-candidate', {
            'candidate': candidate,
            'from_sid': sender_sid,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error relaying ICE candidate: {e}")
        # Don't emit error, just log it

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
        
        print(f"📢 Teacher starting broadcast in room: {room_id}")
        
        student_sids = list(room['students'].keys())
        
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
        'room_authority': room_authority,
        'sessions': sessions,
        'total_rooms': len(rooms),
        'total_connections': len(sessions)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 WebRTC Broadcast System - SIGNALING ONLY")
    print("🌟 NELAVISTA LIVE Phase 1: Teacher Authority Enabled")
    print(f"{'='*60}")
    print("✅ Backend handles signaling only (NO TURN/STUN config)")
    print("✅ Teacher authority features implemented:")
    print("   • Mute/Unmute all students")
    print("   • Disable/Enable all cameras")
    print("   • Approve microphone requests")
    print("   • Receive student struggle signals")
    print("   • Control question visibility")
    print("✅ TURN configuration is frontend-only")
    print("✅ Production ready for Render deployment")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
