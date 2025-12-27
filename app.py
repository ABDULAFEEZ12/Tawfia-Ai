"""
app.py - SIMPLIFIED, WORKING VERSION
ONE signaling path, ONE join path, CLEAN WebRTC
"""

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
import json
from datetime import datetime
from flask import Flask, render_template, session, redirect, url_for, request, flash
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

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
    teacher_id = db.Column(db.String(80))
    teacher_name = db.Column(db.String(80))
    student_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()
    print("✅ Database tables created")

# ============================================
# In-Memory Storage (Simple & Clean)
# ============================================
# Room state: room_id -> {teacher_sid: str, students: set(sid)}
rooms = {}
# User sessions: sid -> {room: str, role: str, user_id: str}
sessions = {}

# ============================================
# Socket.IO Event Handlers (SIMPLE)
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    sessions[sid] = {'room': None, 'role': None, 'user_id': None}
    print(f"✅ Connected: {sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sessions:
        session_data = sessions[sid]
        room_id = session_data.get('room')
        
        if room_id and room_id in rooms:
            # Remove from room
            if session_data.get('role') == 'teacher':
                # Teacher disconnected - notify all students
                emit('teacher-disconnected', room=room_id)
                del rooms[room_id]
            else:
                # Student disconnected
                if sid in rooms[room_id]['students']:
                    rooms[room_id]['students'].remove(sid)
                    rooms[room_id]['student_count'] = len(rooms[room_id]['students'])
                    # Notify teacher
                    teacher_sid = rooms[room_id].get('teacher_sid')
                    if teacher_sid:
                        emit('student-left', {'sid': sid}, room=teacher_sid)
            
            # Clean up empty rooms
            if room_id in rooms and not rooms[room_id]['students'] and not rooms[room_id].get('teacher_sid'):
                del rooms[room_id]
        
        del sessions[sid]
    print(f"❌ Disconnected: {sid}")

@socketio.on('join-room')
def handle_join_room(data):
    """SIMPLE: One join path for both teacher and student"""
    try:
        sid = request.sid
        room_id = data.get('room')
        role = data.get('role', 'student')  # 'teacher' or 'student'
        user_id = data.get('user_id', sid)
        username = data.get('username', 'User')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👤 {role} joining room: {room_id}")
        
        # Join the Socket.IO room
        join_room(room_id)
        
        # Initialize room if it doesn't exist
        if room_id not in rooms:
            rooms[room_id] = {
                'teacher_sid': None,
                'students': set(),
                'student_count': 0
            }
        
        # Update session
        sessions[sid]['room'] = room_id
        sessions[sid]['role'] = role
        sessions[sid]['user_id'] = user_id
        sessions[sid]['username'] = username
        
        if role == 'teacher':
            # Teacher joining
            rooms[room_id]['teacher_sid'] = sid
            
            # Create room in database if it doesn't exist
            with app.app_context():
                existing_room = Room.query.get(room_id)
                if not existing_room:
                    room = Room(
                        id=room_id,
                        teacher_id=user_id,
                        teacher_name=username,
                        is_active=True
                    )
                    db.session.add(room)
                    db.session.commit()
            
            emit('room-joined', {
                'role': 'teacher',
                'room': room_id,
                'message': 'You are now the teacher'
            })
            
            print(f"✅ Teacher joined room: {room_id}")
            
        else:
            # Student joining
            # Check if teacher is present
            teacher_sid = rooms[room_id].get('teacher_sid')
            
            if not teacher_sid:
                emit('error', {'message': 'Teacher not in room. Please wait.'})
                return
            
            # Add student to room
            rooms[room_id]['students'].add(sid)
            rooms[room_id]['student_count'] = len(rooms[room_id]['students'])
            
            # Notify student
            emit('room-joined', {
                'role': 'student',
                'room': room_id,
                'message': 'Joined classroom successfully'
            })
            
            # Notify teacher
            emit('student-joined', {
                'sid': sid,
                'username': username,
                'user_id': user_id
            }, room=teacher_sid)
            
            print(f"✅ Student joined room: {room_id} (Total: {rooms[room_id]['student_count']})")
        
    except Exception as e:
        print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# ============================================
# WebRTC Signaling (ONE PATH - NO DUPLICATION)
# ============================================

@socketio.on('rtc-offer')
def handle_rtc_offer(data):
    """Teacher sends offer to student"""
    try:
        room_id = data.get('room')
        offer = data.get('offer')
        target_sid = data.get('target_sid')  # Student's socket ID
        
        if not all([room_id, offer, target_sid]):
            emit('error', {'message': 'Missing offer data'})
            return
        
        print(f"🎥 RTC offer from teacher to {target_sid}")
        
        # Forward offer to specific student
        emit('rtc-offer', {
            'offer': offer,
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
        
        # Find teacher in room
        if room_id in rooms:
            teacher_sid = rooms[room_id].get('teacher_sid')
            if teacher_sid:
                print(f"🎥 RTC answer from student to teacher")
                emit('rtc-answer', {
                    'answer': answer,
                    'sid': request.sid
                }, room=teacher_sid)
            else:
                emit('error', {'message': 'Teacher not found'})
        
    except Exception as e:
        print(f"❌ Error in rtc-answer: {e}")
        emit('error', {'message': str(e)})

@socketio.on('rtc-ice-candidate')
def handle_rtc_ice_candidate(data):
    """Exchange ICE candidates"""
    try:
        room_id = data.get('room')
        candidate = data.get('candidate')
        target_sid = data.get('target_sid')
        
        if not all([room_id, candidate, target_sid]):
            emit('error', {'message': 'Missing ICE candidate data'})
            return
        
        print(f"❄️ ICE candidate to {target_sid}")
        
        # Forward ICE candidate to target
        emit('rtc-ice-candidate', {
            'candidate': candidate,
            'sid': request.sid
        }, room=target_sid)
        
    except Exception as e:
        print(f"❌ Error in rtc-ice-candidate: {e}")
        emit('error', {'message': str(e)})

# ============================================
# Control Events
# ============================================

@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students"""
    try:
        room_id = data.get('room')
        
        if room_id in rooms:
            teacher_sid = rooms[room_id].get('teacher_sid')
            if teacher_sid == request.sid:
                # Notify all students in room
                emit('broadcast-started', {
                    'message': 'Teacher started broadcasting'
                }, room=room_id, skip_sid=teacher_sid)
                print(f"📢 Broadcast started in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in start-broadcast: {e}")

@socketio.on('stop-broadcast')
def handle_stop_broadcast(data):
    """Teacher stops broadcasting"""
    try:
        room_id = data.get('room')
        
        if room_id in rooms:
            teacher_sid = rooms[room_id].get('teacher_sid')
            if teacher_sid == request.sid:
                emit('broadcast-stopped', room=room_id, skip_sid=teacher_sid)
                print(f"📢 Broadcast stopped in room: {room_id}")
        
    except Exception as e:
        print(f"❌ Error in stop-broadcast: {e}")

# ============================================
# Flask Routes (Simple)
# ============================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/teacher/<room_id>')
def teacher_view(room_id):
    return render_template('teacher.html', room_id=room_id)

@app.route('/student/<room_id>')
def student_view(room_id):
    return render_template('student.html', room_id=room_id)

@app.route('/create-room')
def create_room():
    import uuid
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/teacher/{room_id}')

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 SIMPLIFIED WebRTC Broadcast System")
    print(f"{'='*60}")
    print("✅ One signaling path")
    print("✅ One join path")
    print("✅ Clean WebRTC")
    print("✅ Ready for 1→N broadcasting")
    print(f"{'='*60}\n")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
