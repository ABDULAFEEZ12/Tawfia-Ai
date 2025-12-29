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
student_preferences = {}

# ============================================
# Helper Functions
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'teacher_sid': None,
            'teacher_name': None,
            'students': {},  # sid -> username
            'student_data': {},  # sid -> {hasVideo, hasAudio, preferences}
            'controls': {
                'cameras_disabled': False,
                'mics_disabled': False,
                'questions_enabled': True,
                'slides_enabled': True,
                'class_started': False
            },
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def get_room_student_data(room_id, student_sid):
    """Get student data from room"""
    if room_id in rooms and student_sid in rooms[room_id]['student_data']:
        return rooms[room_id]['student_data'][student_sid]
    return None

def cleanup_room(room_id):
    """Remove empty rooms"""
    if room_id in rooms:
        room = rooms[room_id]
        if not room['teacher_sid'] and not room['students']:
            del rooms[room_id]
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
        
        if room_id and room_id in rooms:
            room = rooms[room_id]
            
            if session_data['role'] == 'teacher':
                # Teacher disconnected
                room['teacher_sid'] = None
                room['teacher_name'] = None
                with app.app_context():
                    room_db = Room.query.get(room_id)
                    if room_db:
                        room_db.teacher_id = None
                        db.session.commit()
                
                # Notify all students
                for student_sid in room['students']:
                    emit('teacher-disconnected', room=student_sid)
                    
            elif session_data['role'] == 'student':
                # Student disconnected
                if sid in room['students']:
                    username = room['students'][sid]
                    del room['students'][sid]
                    if sid in room['student_data']:
                        del room['student_data'][sid]
                    
                    # Notify teacher
                    if room['teacher_sid']:
                        emit('student-left', {
                            'studentSid': sid,
                            'studentName': username
                        }, room=room['teacher_sid'])
            
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
        preferences = data.get('preferences', {})
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👤 {username} ({role}) joining room: {room_id}")
        
        room = get_or_create_room(room_id)
        
        if role == 'teacher':
            if room['teacher_sid']:
                emit('error', {'message': 'Room already has a teacher'})
                return
            
            room['teacher_sid'] = sid
            room['teacher_name'] = username
            
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
            room['students'][sid] = username
            room['student_data'][sid] = {
                'hasVideo': False,
                'hasAudio': False,
                'preferences': preferences
            }
            
            emit('room-joined', {
                'role': 'student',
                'room': room_id,
                'message': 'Joined classroom successfully',
                'sid': sid,
                'teacher_sid': room['teacher_sid'],
                'teacher_name': room['teacher_name'],
                'teacher_controls': room['controls']
            })
            
            # Notify teacher about new student
            if room['teacher_sid']:
                emit('student-joined', {
                    'studentName': username,
                    'studentSid': sid
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
# WebRTC Signaling - FIXED VERSION
# ============================================
@socketio.on('teacher-ready')
def handle_teacher_ready(data):
    """Teacher is ready and sends offer to all students"""
    try:
        room_id = data.get('room')
        teacher_sid = data.get('teacher_sid')
        teacher_name = data.get('teacher_name')
        
        if not room_id or room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        
        # Verify sender is the teacher
        if request.sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can send ready signal'})
            return
        
        print(f"📢 Teacher {teacher_name} ready in room: {room_id}")
        
        # Notify all students that teacher is ready
        for student_sid in room['students']:
            emit('teacher-ready', {
                'teacher_sid': teacher_sid,
                'teacher_name': teacher_name,
                'room': room_id
            }, room=student_sid)
        
    except Exception as e:
        print(f"❌ Error in teacher-ready: {e}")
        emit('error', {'message': str(e)})

@socketio.on('rtc-offer')
def handle_rtc_offer(data):
    """Handle RTC offer - can be from teacher to student OR student to teacher"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        offer = data.get('offer')
        from_sid = request.sid
        
        if not all([room_id, target_sid, offer]):
            print(f"❌ Missing RTC offer data: room={room_id}, target={target_sid}")
            return
        
        if room_id not in rooms:
            print(f"❌ Room {room_id} not found for RTC offer")
            return
        
        room = rooms[room_id]
        
        # Check if sender is authorized (teacher or student in this room)
        sender_is_teacher = (from_sid == room['teacher_sid'])
        sender_is_student = (from_sid in room['students'])
        
        if not (sender_is_teacher or sender_is_student):
            print(f"❌ Unauthorized sender for RTC offer: {from_sid}")
            return
        
        # Check if target exists in room
        target_is_teacher = (target_sid == room['teacher_sid'])
        target_is_student = (target_sid in room['students'])
        
        if not (target_is_teacher or target_is_student):
            print(f"❌ Target not found for RTC offer: {target_sid}")
            return
        
        print(f"🎥 RTC offer from {from_sid} to {target_sid} in room {room_id}")
        
        # Forward offer to target
        emit('rtc-offer', {
            'offer': offer,
            'from_sid': from_sid,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error in rtc-offer: {e}")

@socketio.on('rtc-answer')
def handle_rtc_answer(data):
    """Handle RTC answer"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        answer = data.get('answer')
        student_name = data.get('studentName')
        student_sid = request.sid
        hasVideo = data.get('hasVideo', False)
        hasAudio = data.get('hasAudio', False)
        
        if not all([room_id, answer]):
            print(f"❌ Missing RTC answer data")
            return
        
        if room_id not in rooms:
            print(f"❌ Room {room_id} not found for RTC answer")
            return
        
        room = rooms[room_id]
        
        # Check if sender is authorized
        sender_is_teacher = (student_sid == room['teacher_sid'])
        sender_is_student = (student_sid in room['students'])
        
        if not (sender_is_teacher or sender_is_student):
            print(f"❌ Unauthorized sender for RTC answer: {student_sid}")
            return
        
        # If no target_sid provided, assume answer is for teacher
        if not target_sid and sender_is_student:
            target_sid = room['teacher_sid']
        
        # Check if target exists
        if target_sid not in [room['teacher_sid']] + list(room['students'].keys()):
            print(f"❌ Target not found for RTC answer: {target_sid}")
            return
        
        # Update student data if this is from a student
        if sender_is_student and student_sid in room['student_data']:
            room['student_data'][student_sid]['hasVideo'] = hasVideo
            room['student_data'][student_sid]['hasAudio'] = hasAudio
            print(f"📹 Student {student_name or room['students'][student_sid]} hasVideo={hasVideo}, hasAudio={hasAudio}")
        
        print(f"🎥 RTC answer from {student_sid} to {target_sid} in room {room_id}")
        
        # Forward answer to target
        emit('rtc-answer', {
            'answer': answer,
            'from_sid': student_sid,
            'studentName': student_name or room['students'].get(student_sid, 'Student'),
            'hasVideo': hasVideo,
            'hasAudio': hasAudio,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error in rtc-answer: {e}")

@socketio.on('rtc-ice-candidate')
def handle_rtc_ice_candidate(data):
    """Exchange ICE candidates"""
    try:
        room_id = data.get('room')
        candidate = data.get('candidate')
        target_sid = data.get('target_sid')
        
        if not all([room_id, candidate, target_sid]):
            print(f"⚠️ Incomplete ICE candidate data")
            return
        
        if room_id not in rooms:
            print(f"❌ Room {room_id} not found for ICE candidate")
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        # Verify sender is in room
        sender_is_teacher = (sender_sid == room['teacher_sid'])
        sender_is_student = (sender_sid in room['students'])
        
        if not (sender_is_teacher or sender_is_student):
            print(f"❌ Unauthorized sender for ICE candidate: {sender_sid}")
            return
        
        # Verify target is in room
        target_is_teacher = (target_sid == room['teacher_sid'])
        target_is_student = (target_sid in room['students'])
        
        if not (target_is_teacher or target_is_student):
            print(f"❌ Target not found for ICE candidate: {target_sid}")
            return
        
        # Relay candidate
        emit('rtc-ice-candidate', {
            'candidate': candidate,
            'from_sid': sender_sid,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error in rtc-ice-candidate: {e}")

# ============================================
# Teacher Control Events
# ============================================
@socketio.on('teacher-control-update')
def handle_teacher_control_update(data):
    """Teacher updates classroom controls"""
    try:
        room_id = data.get('room')
        control = data.get('control')
        value = data.get('value')
        
        if not all([room_id, control]):
            emit('error', {'message': 'Missing control data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can update controls'})
            return
        
        # Update control
        room['controls'][control] = value
        
        # Broadcast to all students
        for student_sid in room['students']:
            emit('teacher-control-update', {
                'control': control,
                'value': value,
                'room': room_id
            }, room=student_sid)
        
        print(f"⚙️ Teacher updated control {control} to {value} in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-control-update: {e}")
        emit('error', {'message': str(e)})

@socketio.on('teacher-force-media-control')
def handle_teacher_force_media_control(data):
    """Teacher forces media control on student(s)"""
    try:
        room_id = data.get('room')
        control = data.get('control')  # 'camera' or 'microphone'
        enabled = data.get('enabled', True)
        reason = data.get('reason', '')
        
        if not all([room_id, control]):
            emit('error', {'message': 'Missing control data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can force media controls'})
            return
        
        target_student_sid = data.get('targetStudentSid')
        
        if target_student_sid:
            # Send to specific student
            if target_student_sid in room['students']:
                emit('teacher-force-media-control', {
                    'control': control,
                    'enabled': enabled,
                    'reason': reason,
                    'room': room_id
                }, room=target_student_sid)
                print(f"🎛️ Teacher forced {control} {enabled} for student {target_student_sid}")
        else:
            # Send to all students
            for student_sid in room['students']:
                emit('teacher-force-media-control', {
                    'control': control,
                    'enabled': enabled,
                    'reason': reason,
                    'room': room_id
                }, room=student_sid)
            print(f"🎛️ Teacher forced {control} {enabled} for all students")
        
    except Exception as e:
        print(f"❌ Error in teacher-force-media-control: {e}")
        emit('error', {'message': str(e)})

@socketio.on('hand-acknowledged')
def handle_hand_acknowledged(data):
    """Teacher acknowledges a raised hand"""
    try:
        room_id = data.get('room')
        student_name = data.get('studentName')
        student_sid = data.get('studentSid')
        acknowledged_by = data.get('acknowledgedBy')
        
        if not all([room_id, student_sid]):
            emit('error', {'message': 'Missing data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can acknowledge hands'})
            return
        
        # Send acknowledgement to student
        emit('hand-acknowledged', {
            'studentName': student_name,
            'acknowledgedBy': acknowledged_by,
            'room': room_id
        }, room=student_sid)
        
        print(f"✋ Teacher acknowledged hand from {student_name}")
        
    except Exception as e:
        print(f"❌ Error in hand-acknowledged: {e}")
        emit('error', {'message': str(e)})

# ============================================
# Student Action Events
# ============================================
@socketio.on('student-action')
def handle_student_action(data):
    """Student performs an action (raise hand, struggle signal, etc.)"""
    try:
        room_id = data.get('room')
        action = data.get('action')
        
        if not all([room_id, action]):
            emit('error', {'message': 'Missing action data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        if student_sid not in room['students']:
            emit('error', {'message': 'Not authorized'})
            return
        
        student_name = room['students'][student_sid]
        data['studentName'] = student_name
        data['studentSid'] = student_sid
        
        # Forward to teacher
        if room['teacher_sid']:
            emit('student-action', data, room=room['teacher_sid'])
        
        print(f"🎯 Student {student_name} performed action: {action}")
        
    except Exception as e:
        print(f"❌ Error in student-action: {e}")
        emit('error', {'message': str(e)})

@socketio.on('student-media-update')
def handle_student_media_update(data):
    """Student updates their media status"""
    try:
        room_id = data.get('room')
        media_type = data.get('mediaType')  # 'camera' or 'microphone'
        enabled = data.get('enabled')
        student_name = data.get('studentName')
        
        if not all([room_id, media_type, enabled is not None]):
            emit('error', {'message': 'Missing media update data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        if student_sid not in room['students']:
            emit('error', {'message': 'Not authorized'})
            return
        
        # Update student data
        if student_sid in room['student_data']:
            if media_type == 'camera':
                room['student_data'][student_sid]['hasVideo'] = enabled
            elif media_type == 'microphone':
                room['student_data'][student_sid]['hasAudio'] = enabled
        
        # Forward to teacher
        if room['teacher_sid']:
            emit('student-media-update', {
                'mediaType': media_type,
                'enabled': enabled,
                'studentName': student_name or room['students'][student_sid],
                'studentSid': student_sid,
                'room': room_id
            }, room=room['teacher_sid'])
        
        print(f"📹 Student {room['students'][student_sid]} updated {media_type} to {enabled}")
        
    except Exception as e:
        print(f"❌ Error in student-media-update: {e}")
        emit('error', {'message': str(e)})

@socketio.on('student-preferences')
def handle_student_preferences(data):
    """Student updates their preferences"""
    try:
        room_id = data.get('room')
        preferences = data.get('preferences', {})
        
        if not room_id:
            emit('error', {'message': 'Missing room ID'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        student_sid = request.sid
        
        if student_sid not in room['students']:
            emit('error', {'message': 'Not authorized'})
            return
        
        # Update preferences
        if student_sid in room['student_data']:
            if 'preferences' not in room['student_data'][student_sid]:
                room['student_data'][student_sid]['preferences'] = {}
            room['student_data'][student_sid]['preferences'].update(preferences)
        
        print(f"⚙️ Student {room['students'][student_sid]} updated preferences")
        
    except Exception as e:
        print(f"❌ Error in student-preferences: {e}")
        emit('error', {'message': str(e)})

# ============================================
# AI and Content Events
# ============================================
@socketio.on('ai-summary')
def handle_ai_summary(data):
    """Send AI summary to students"""
    try:
        room_id = data.get('room')
        summary = data.get('summary')
        summary_type = data.get('type', 'note')
        
        if not all([room_id, summary]):
            emit('error', {'message': 'Missing AI summary data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        # Verify sender is teacher
        if sender_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can send AI summaries'})
            return
        
        # Send to all students with AI listener enabled
        timestamp = datetime.utcnow().isoformat()
        for student_sid in room['students']:
            student_data = room['student_data'].get(student_sid, {})
            preferences = student_data.get('preferences', {})
            
            if preferences.get('aiListener', False):
                emit('ai-summary', {
                    'summary': summary,
                    'type': summary_type,
                    'timestamp': timestamp,
                    'room': room_id
                }, room=student_sid)
        
        print(f"🤖 AI summary sent to students in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in ai-summary: {e}")
        emit('error', {'message': str(e)})

@socketio.on('slide-update')
def handle_slide_update(data):
    """Update slide for students"""
    try:
        room_id = data.get('room')
        slide_url = data.get('slide_url')
        slide_number = data.get('slide_number')
        total_slides = data.get('total_slides')
        slide_title = data.get('slide_title')
        
        if not all([room_id, slide_url]):
            emit('error', {'message': 'Missing slide data'})
            return
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        # Verify sender is teacher
        if sender_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can update slides'})
            return
        
        # Send to all students with slides enabled
        for student_sid in room['students']:
            student_data = room['student_data'].get(student_sid, {})
            preferences = student_data.get('preferences', {})
            
            if preferences.get('showSlides', False):
                emit('slide-update', {
                    'slide_url': slide_url,
                    'slide_number': slide_number,
                    'total_slides': total_slides,
                    'slide_title': slide_title,
                    'room': room_id
                }, room=student_sid)
        
        print(f"📊 Slide updated in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in slide-update: {e}")
        emit('error', {'message': str(e)})

# ============================================
# System Events
# ============================================
@socketio.on('ping')
def handle_ping(data):
    """Keep-alive ping"""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

@socketio.on('teacher-disconnected')
def handle_teacher_disconnect_broadcast(data):
    """Teacher explicitly disconnects (ends class)"""
    try:
        room_id = data.get('room')
        
        if room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        # Notify all students
        for student_sid in room['students']:
            emit('teacher-disconnected', {
                'message': data.get('message', 'Class ended by teacher'),
                'room': room_id
            }, room=student_sid)
        
        # Clean up room
        room['teacher_sid'] = None
        room['teacher_name'] = None
        
        print(f"🛑 Teacher ended class in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in teacher-disconnected: {e}")

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
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 WebRTC Classroom System - FIXED SIGNALING")
    print(f"{'='*60}")
    print("✅ Bidirectional WebRTC connections fixed")
    print("✅ Teacher can now see/hear students")
    print("✅ Students can now see/hear teacher")
    print("✅ Production ready for Render deployment")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, log_output=True)
