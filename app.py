"""
app.py - SIMPLIFIED, WORKING VERSION
ONE signaling path, CLEAN WebRTC
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
    teacher_sid = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()
    print("✅ Database tables created")

# ============================================
# In-Memory Storage (Simple & Clean)
# ============================================
# Structure: {room_id: {'teacher_sid': sid, 'students': {sid: username}}}
rooms = {}
# Structure: {sid: {'room': room_id, 'role': 'teacher'|'student', 'username': name}}
sessions = {}

# ============================================
# Helper Functions
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'teacher_sid': None,
            'students': {},  # sid -> username
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def cleanup_room(room_id):
    """Remove empty rooms"""
    if room_id in rooms:
        room = rooms[room_id]
        if not room['teacher_sid'] and not room['students']:
            del rooms[room_id]
            # Also remove from database
            with app.app_context():
                Room.query.filter_by(id=room_id).delete()
                db.session.commit()

# ============================================
# Socket.IO Event Handlers (SIMPLE)
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
                # Notify all students
                for student_sid in room['students']:
                    emit('teacher-disconnected', room=student_sid)
            elif session_data['role'] == 'student':
                # Student disconnected
                if sid in room['students']:
                    del room['students'][sid]
                    # Notify teacher
                    if room['teacher_sid']:
                        emit('student-left', {'sid': sid}, room=room['teacher_sid'])
            
            # Clean up empty room
            cleanup_room(room_id)
        
        del sessions[sid]
    print(f"❌ Client disconnected: {sid}")

@socketio.on('join-room')
def handle_join_room(data):
    """SIMPLE: One join path for both teacher and student"""
    try:
        sid = request.sid
        room_id = data.get('room')
        role = data.get('role', 'student')  # 'teacher' or 'student'
        username = data.get('username', 'User' if role == 'teacher' else 'Student')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        print(f"👤 {username} ({role}) joining room: {room_id}")
        
        # Get or create room
        room = get_or_create_room(room_id)
        
        if role == 'teacher':
            # Teacher joining
            if room['teacher_sid']:
                emit('error', {'message': 'Room already has a teacher'})
                return
            
            room['teacher_sid'] = sid
            
            # Save to database
            with app.app_context():
                existing_room = Room.query.get(room_id)
                if not existing_room:
                    room_db = Room(
                        id=room_id,
                        teacher_sid=sid,
                        teacher_name=username,
                        is_active=True
                    )
                    db.session.add(room_db)
                    db.session.commit()
            
            emit('room-joined', {
                'role': 'teacher',
                'room': room_id,
                'message': 'You are now the teacher'
            })
            
            print(f"✅ Teacher joined room: {room_id}")
            
        else:
            # Student joining
            if not room['teacher_sid']:
                emit('error', {'message': 'Teacher not in room. Please wait.'})
                return
            
            # Add student to room
            room['students'][sid] = username
            
            emit('room-joined', {
                'role': 'student',
                'room': room_id,
                'message': 'Joined classroom successfully'
            })
            
            # Notify teacher
            emit('student-joined', {
                'sid': sid,
                'username': username
            }, room=room['teacher_sid'])
            
            print(f"✅ Student joined room: {room_id}")
        
        # Update session
        sessions[sid]['room'] = room_id
        sessions[sid]['role'] = role
        sessions[sid]['username'] = username
        
        # Join Socket.IO room
        join_room(room_id)
        
    except Exception as e:
        print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# ============================================
# WebRTC Signaling (SINGLE PATH - NO DUPLICATION)
# ============================================

@socketio.on('rtc-offer')
def handle_rtc_offer(data):
    """Teacher sends offer to specific student"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')  # Student's socket ID
        offer = data.get('offer')
        
        if not all([room_id, target_sid, offer]):
            emit('error', {'message': 'Missing offer data'})
            return
        
        # Verify teacher is in room
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        if request.sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can send offers'})
            return
        
        # Verify target is a student in this room
        if target_sid not in room['students']:
            emit('error', {'message': 'Student not found in room'})
            return
        
        print(f"🎥 Teacher sending offer to student {target_sid}")
        
        # Forward offer to student
        emit('rtc-offer', {
            'offer': offer,
            'from_teacher': True
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
        
        # Verify room exists
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        # Verify sender is a student in this room
        if sender_sid not in room['students']:
            emit('error', {'message': 'Not authorized'})
            return
        
        # Verify teacher exists
        if not room['teacher_sid']:
            emit('error', {'message': 'Teacher not available'})
            return
        
        print(f"🎥 Student {sender_sid} sending answer to teacher")
        
        # Forward answer to teacher
        emit('rtc-answer', {
            'answer': answer,
            'from_student': sender_sid
        }, room=room['teacher_sid'])
        
    except Exception as e:
        print(f"❌ Error in rtc-answer: {e}")
        emit('error', {'message': str(e)})

@socketio.on('rtc-ice-candidate')
def handle_rtc_ice_candidate(data):
    """Exchange ICE candidates between teacher and student"""
    try:
        room_id = data.get('room')
        candidate = data.get('candidate')
        target_sid = data.get('target_sid')
        
        if not all([room_id, candidate, target_sid]):
            emit('error', {'message': 'Missing ICE candidate data'})
            return
        
        # Verify room exists
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        sender_sid = request.sid
        
        # Verify sender is in room (either teacher or student)
        is_teacher = (sender_sid == room['teacher_sid'])
        is_student = (sender_sid in room['students'])
        
        if not (is_teacher or is_student):
            emit('error', {'message': 'Not authorized'})
            return
        
        # Verify target is in room
        is_target_teacher = (target_sid == room['teacher_sid'])
        is_target_student = (target_sid in room['students'])
        
        if not (is_target_teacher or is_target_student):
            emit('error', {'message': 'Target not found in room'})
            return
        
        print(f"❄️ ICE candidate from {sender_sid} to {target_sid}")
        
        # Forward ICE candidate to target
        emit('rtc-ice-candidate', {
            'candidate': candidate,
            'from_sid': sender_sid
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
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        if request.sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can start broadcast'})
            return
        
        print(f"📢 Teacher starting broadcast in room: {room_id}")
        
        # Get list of student SIDs
        student_sids = list(room['students'].keys())
        
        # Notify teacher with student list
        emit('broadcast-ready', {
            'student_sids': student_sids,
            'student_count': len(student_sids)
        })
        
        # Notify students
        for student_sid in student_sids:
            emit('teacher-ready', room=student_sid)
        
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
    """Home page - redirect to teacher or show join form"""
    return render_template('index.html')

@app.route('/teacher')
def teacher_create():
    """Create new room as teacher"""
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/teacher/{room_id}')

@app.route('/teacher/<room_id>')
def teacher_view(room_id):
    """Teacher dashboard"""
    return render_template('teacher.html', room_id=room_id)

@app.route('/student/<room_id>')
def student_view(room_id):
    """Student join page"""
    return render_template('student.html', room_id=room_id)

@app.route('/join', methods=['POST'])
def join_room_post():
    """Join room via form"""
    room_id = request.form.get('room_id', '').strip()
    if not room_id:
        flash('Please enter a room ID')
        return redirect('/')
    return redirect(f'/student/{room_id}')

# ============================================
# NEW: Live Meeting Routes (Responsive Interface)
# ============================================

@app.route('/live_meeting')
def live_meeting():
    """Landing page for live meeting with role selection"""
    return render_template('live_meeting.html')

@app.route('/live_meeting/teacher')
def live_meeting_teacher_create():
    """Create new meeting as teacher"""
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/live_meeting/teacher/{room_id}')

@app.route('/live_meeting/teacher/<room_id>')
def live_meeting_teacher_view(room_id):
    """Modern teacher interface - using teacher_live.html"""
    return render_template('teacher_live.html', room_id=room_id)

@app.route('/live_meeting/student/<room_id>')
def live_meeting_student_view(room_id):
    """Modern student interface - using student_live.html"""
    return render_template('student_live.html', room_id=room_id)

@app.route('/live_meeting/join', methods=['POST'])
def live_meeting_join():
    """Join meeting via form (modern interface)"""
    room_id = request.form.get('room_id', '').strip()
    username = request.form.get('username', '').strip()
    
    if not room_id:
        flash('Please enter a meeting ID')
        return redirect('/live_meeting')
    
    if not username:
        username = f"Student_{str(uuid.uuid4())[:4]}"
    
    # Store username in session for later use
    session['live_username'] = username
    
    return redirect(f'/live_meeting/student/{room_id}')

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 SIMPLIFIED WebRTC Broadcast System")
    print(f"{'='*60}")
    print("✅ Clean signaling: rtc-offer, rtc-answer, rtc-ice-candidate")
    print("✅ One join path: join-room")
    print("✅ Teacher → Many Students architecture")
    print("✅ NEW: /live_meeting routes added")
    print("✅ Using templates: live_meeting.html, teacher_live.html, student_live.html")
    print("✅ Ready for production")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
