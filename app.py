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
from flask_socketio import SocketIO, join_room, emit
import requests
from flask_sqlalchemy import SQLAlchemy
import speech_recognition as sr
import sqlite3

# Load environment variables
load_dotenv()

print("✅ API KEY:", os.getenv("GOOGLE_NEWS_API_KEY"))
print("✅ CX:", os.getenv("GOOGLE_CX"))

# Initialize Flask app
app = Flask(__name__)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = 'tawfiq-ai-secret-key-2024'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database Configuration - SQLite as primary, PostgreSQL as optional fallback
DATABASE_URL = os.getenv('DATABASE_URL')
USE_POSTGRES = False

if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    try:
        # Test PostgreSQL connection
        import psycopg2
        from urllib.parse import urlparse
        
        # Parse the database URL
        parsed = urlparse(DATABASE_URL)
        
        # Reconstruct URL for psycopg2
        dbname = parsed.path[1:]
        user = parsed.username
        password = parsed.password
        host = parsed.hostname
        port = parsed.port or 5432
        
        # Test connection
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
            connect_timeout=5
        )
        conn.close()
        
        app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
        USE_POSTGRES = True
        print("✅ PostgreSQL database connected successfully")
    except Exception as e:
        print(f"⚠️ PostgreSQL connection failed, falling back to SQLite: {e}")
        # Fallback to SQLite
        base_dir = os.path.dirname(os.path.abspath(__file__))
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(base_dir, "tawfiqai.db")}'
        print("✅ Using SQLite database as fallback")
else:
    # Use SQLite by default
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(base_dir, "tawfiqai.db")}'
    print("✅ Using SQLite database")

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

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

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created successfully")
        
        # Create default user if not exists
        user = User.query.filter_by(username='zayd').first()
        if not user:
            user = User(username='zayd', email='zayd@example.com')
            user.set_password('secure123')
            db.session.add(user)
            db.session.commit()
            print("✅ Default user created")
    except Exception as e:
        print(f"⚠️ Database error: {e}")
        # If SQLAlchemy fails, try direct SQLite connection
        try:
            if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
                conn = sqlite3.connect('tawfiqai.db')
                cursor = conn.cursor()
                
                # Create users table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        level INTEGER DEFAULT 1,
                        joined_on DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_login DATETIME
                    )
                ''')
                
                # Create user_questions table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        question TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Check for default user
                cursor.execute("SELECT * FROM user WHERE username = 'zayd'")
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO user (username, email, password_hash) VALUES (?, ?, ?)",
                        ('zayd', 'zayd@example.com', generate_password_hash('secure123'))
                    )
                
                conn.commit()
                conn.close()
                print("✅ SQLite database initialized directly")
        except Exception as sqlite_error:
            print(f"⚠️ SQLite initialization also failed: {sqlite_error}")

# --- Get Questions for User ---
def get_questions_for_user(username):
    try:
        with app.app_context():
            questions = UserQuestions.query \
                .filter(func.lower(UserQuestions.username) == username.lower()) \
                .order_by(UserQuestions.timestamp.desc()) \
                .all()
            return [
                {
                    "question": q.question,
                    "answer": q.answer,
                    "timestamp": q.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                }
                for q in questions
            ]
    except Exception as e:
        print(f"⚠️ Error getting questions for user: {e}")
        # Try direct SQLite query
        try:
            conn = sqlite3.connect('tawfiqai.db')
            cursor = conn.cursor()
            cursor.execute(
                "SELECT question, answer, timestamp FROM user_questions WHERE username = ? ORDER BY timestamp DESC",
                (username,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [
                {
                    "question": row[0],
                    "answer": row[1],
                    "timestamp": row[2]
                }
                for row in rows
            ]
        except:
            return []

# --- Save a Question and Answer for a User ---
def save_question_and_answer(username, question, answer):
    try:
        with app.app_context():
            # Check if this question already exists for this user
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
        print(f"❌ Failed to save Q&A via SQLAlchemy: {e}")
        # Try direct SQLite save
        try:
            conn = sqlite3.connect('tawfiqai.db')
            cursor = conn.cursor()
            
            # Check if question exists
            cursor.execute(
                "SELECT id FROM user_questions WHERE username = ? AND question = ?",
                (username, question)
            )
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute(
                    "UPDATE user_questions SET answer = ?, timestamp = ? WHERE id = ?",
                    (answer, datetime.utcnow().isoformat(), existing[0])
                )
                print(f"🔁 Updated existing Q&A for '{username}' via SQLite")
            else:
                cursor.execute(
                    "INSERT INTO user_questions (username, question, answer, timestamp) VALUES (?, ?, ?, ?)",
                    (username, question, answer, datetime.utcnow().isoformat())
                )
                print(f"✅ Saved new Q&A for '{username}' via SQLite")
            
            conn.commit()
            conn.close()
        except Exception as sqlite_error:
            print(f"❌ SQLite save also failed: {sqlite_error}")

# Continue with the rest of your app.py (Redis setup, JSON loading, routes, etc.)
# ... [Rest of your code remains the same from the previous version]

# --- Redis Cache Setup ---
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))
redis_db = int(os.getenv("REDIS_DB", 0))
redis_password = os.getenv("REDIS_PASSWORD", None)

try:
    r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
    r.ping()
    print("✅ Redis connected successfully")
except Exception as e:
    print(f"⚠️ Redis connection failed: {e}")
    r = None

# --- File-Based Cache ---
CACHE_FILE = "tawfiq_cache.json"

# Load cache from file
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            question_cache = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        question_cache = {}
else:
    question_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(question_cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error saving cache: {e}")

# --- Load JSON datasets ---
def load_json_data(file_name, data_variable_name):
    data = {}
    # Try multiple paths
    paths_to_try = [
        os.path.join(os.path.dirname(__file__), 'DATA', file_name),
        os.path.join(os.path.dirname(__file__), 'static', 'DATA', file_name),
        os.path.join(os.path.dirname(__file__), 'static', 'data', file_name),
        file_name  # Try direct path
    ]
    
    for file_path in paths_to_try:
        print(f"Trying to load {data_variable_name} from: {file_path}")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                print(f"✅ Successfully loaded {data_variable_name} data from {file_path}")
                return data
            except Exception as e:
                print(f"❌ Error loading {file_path}: {e}")
    
    print(f"❌ Could not load {data_variable_name} data from any path")
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
    print("⚠️ Some AI features may not work properly.")

# --- Flask Routes ---
# ... [All your routes remain exactly the same as in the previous version]
# I'll include the most critical ones, but you should copy all your routes from the previous working version

@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    username = user['username']
    questions = get_questions_for_user(username)

    return render_template('index.html', user=user, questions=questions)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password or not email:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))

        try:
            # Check if username or email already exists
            if User.query.filter_by(username=username).first():
                flash('Username already exists.')
                return redirect(url_for('signup'))

            if User.query.filter_by(email=email).first():
                flash('Email already registered.')
                return redirect(url_for('signup'))

            # Create user
            new_user = User(
                username=username,
                email=email,
                joined_on=datetime.utcnow()
            )
            new_user.set_password(password)

            # Save to database
            db.session.add(new_user)
            db.session.commit()

            # Store user info in session
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
            print(f"Signup error: {e}")
            # Try direct SQLite insert
            try:
                conn = sqlite3.connect('tawfiqai.db')
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute("SELECT id FROM user WHERE username = ? OR email = ?", (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.')
                    conn.close()
                    return redirect(url_for('signup'))
                
                # Insert new user
                cursor.execute(
                    "INSERT INTO user (username, email, password_hash, joined_on) VALUES (?, ?, ?, ?)",
                    (username, email, generate_password_hash(password), datetime.utcnow().isoformat())
                )
                conn.commit()
                conn.close()
                
                # Store in session
                session['user'] = {
                    'username': username,
                    'email': email,
                    'joined_on': datetime.utcnow().strftime('%Y-%m-%d'),
                    'preferred_language': 'English',
                    'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                flash('Account created successfully!')
                return redirect(url_for('index'))
            except Exception as sqlite_error:
                flash(f'Error creating account: {sqlite_error}')
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
            # Try SQLAlchemy first
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
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
                # Try SQLite fallback
                conn = sqlite3.connect('tawfiqai.db')
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT username, email, password_hash, joined_on FROM user WHERE username = ?",
                    (username,)
                )
                row = cursor.fetchone()
                conn.close()
                
                if row and check_password_hash(row[2], password):
                    session.permanent = True
                    session['user'] = {
                        'username': row[0],
                        'email': row[1],
                        'joined_on': row[3][:10] if row[3] else datetime.utcnow().strftime('%Y-%m-%d'),
                        'preferred_language': 'English',
                        'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
            print(f"Login error: {e}")
            if request.is_json:
                return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500
            else:
                flash('Database error. Please try again.')
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

# Questions and levels data
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

@app.route('/my-questions')
def my_questions():
    username = session['user']['username']
    try:
        questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
        return render_template('my_questions.html', questions=questions)
    except Exception as e:
        flash(f'Error loading questions: {str(e)}')
        return render_template('my_questions.html', questions=[])

@app.route('/admin/questions')
def admin_questions():
    try:
        questions = UserQuestions.query.all()
        return render_template('questions.html', questions=questions)
    except Exception as e:
        return f'Error loading questions: {str(e)}'

@app.route('/debug/questions')
def debug_questions():
    try:
        questions = UserQuestions.query.all()
        return '<br>'.join([f"{q.username}: {q.question}" for q in questions])
    except Exception as e:
        return f'Error: {str(e)}'

@app.route('/profile')
def profile():
    user = session.get('user', {})
    return render_template('profile.html',
                           username=user.get('username', 'Guest'),
                           email=user.get('email', 'not_set@example.com'),
                           joined_on=user.get('joined_on', 'Unknown'),
                           preferred_language=user.get('preferred_language', 'English'),
                           last_login=user.get('last_login', 'N/A'))

@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')

        # Update session data
        if 'user' in session:
            session['user']['username'] = username
            session['user']['email'] = email

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

@app.route("/duas")
def all_duas_html():
    DUA_FILE_PATH = os.path.join("static", "data", "duas.json")
    if not os.path.exists(DUA_FILE_PATH):
        return "Dua file not found", 404

    with open(DUA_FILE_PATH, "r", encoding="utf-8") as f:
        try:
            duas_data = json.load(f)
        except json.JSONDecodeError:
            return "Error reading duas.json file", 500

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

@app.route("/live-meeting/<room_id>")
def live_meeting(room_id):
    return render_template("live_meeting.html", room_id=room_id)

@socketio.on("join")
def handle_join(data):
    room = data["room"]
    join_room(room)
    emit("user-joined", data, room=room, include_self=False)

@socketio.on("signal")
def handle_signal(data):
    emit("signal", data, room=data["room"], include_self=False)

@socketio.on("leave")
def handle_leave(data):
    room = data["room"]
    user_id = data["id"]
    emit("user-left", {"id": user_id}, room=room, include_self=False)

@app.route("/duas/json")
def all_duas_json():
    DUA_FILE_PATH = os.path.join("static", "data", "duas.json")
    if not os.path.exists(DUA_FILE_PATH):
        return jsonify({"error": "Dua file not found"}), 404

    with open(DUA_FILE_PATH, "r", encoding="utf-8") as f:
        try:
            duas_data = json.load(f)
        except json.JSONDecodeError:
            return jsonify({"error": "Error reading duas.json file"}), 500

    return jsonify(duas_data)

@app.route('/reminder')
def reminder():
    json_path = os.path.join(os.path.expanduser("~"), "Documents", "Tawfiqai", "DATA", "reminders.json")
    
    if not os.path.exists(json_path):
        json_path = os.path.join(os.path.dirname(__file__), "DATA", "reminders.json")
    
    if not os.path.exists(json_path):
        return "Reminders file not found", 404

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    today = datetime.now().day
    day_key = f"day{today}"
    reminders = data.get(day_key) or data.get("day1", [])
    return render_template('pages/reminder.html', reminders=reminders)

@app.route('/api/reminders')
def get_reminders():
    today = (datetime.utcnow().day % 30) or 30
    json_path = os.path.join(os.path.dirname(__file__), "DATA", "reminders.json")
    
    if os.path.exists(json_path):
        with open(json_path) as f:
            data = json.load(f)
        return jsonify(data.get(f'day{today}', []))
    return jsonify([])

@app.route('/story-time')
def story_time():
    json_path = os.path.join("static", "data", "stories.json")
    if not os.path.exists(json_path):
        json_path = os.path.join(os.path.dirname(__file__), "DATA", "stories.json")
    
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            all_stories = json.load(f)
        return render_template('pages/story_time.html', stories=all_stories)
    return render_template('pages/story_time.html', stories=[])

@app.route('/api/stories')
def get_stories():
    today = (datetime.utcnow().day % 30) or 30
    json_path = os.path.join(os.path.dirname(__file__), "DATA", "stories.json")
    
    if os.path.exists(json_path):
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data.get(f'day{today}', []))
    return jsonify([])

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
        if not os.path.exists(data_path):
            data_path = os.path.join(os.path.dirname(__file__), "DATA", "islamic_motivation.json")
        
        if os.path.exists(data_path):
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

    # 🧠 Default Islamic AI fallback
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

# --- Get Surah List for dropdown ---
@app.route('/get-surah-list')
def get_surah_list():
    surah_list = [
        "Al-Fatihah", "Al-Baqarah", "Aali Imran", "An-Nisa", "Al-Maidah", "Al-Anam", "Al-Araf",
        "Al-Anfal", "At-Tawbah", "Yunus", "Hud", "Yusuf", "Ar-Rad", "Ibrahim", "Al-Hijr",
        "An-Nahl", "Al-Isra", "Al-Kahf", "Maryam", "Ta-Ha", "Al-Anbiya", "Al-Hajj", "Al-Muminun",
        "An-Nur", "Al-Furqan", "Ash-Shuara", "An-Naml", "Al-Qasas", "Al-Ankabut", "Ar-Rum",
        "Luqman", "As-Sajda", "Al-Ahzab", "Saba", "Fatir", "Ya-Sin", "As-Saffat", "Sad",
        "Az-Zumar", "Ghafir", "Fussilat", "Ash-Shura", "Az-Zukhruf", "Ad-Dukhan", "Al-Jathiya",
        "Al-Ahqaf", "Muhammad", "Al-Fath", "Al-Hujurat", "Qaf", "Adh-Dhariyat", "At-Tur",
        "An-Najm", "Al-Qamar", "Ar-Rahman", "Al-Waqi'a", "Al-Hadid", "Al-Mujadila", "Al-Hashr",
        "Al-Mumtahanah", "As-Saff", "Al-Jumu'a", "Al-Munafiqun", "At-Taghabun", "At-Talaq",
        "At-Tahrim", "Al-Mulk", "Al-Qalam", "Al-Haqqah", "Al-Ma'arij", "Nuh", "Al-Jinn",
        "Al-Muzzammil", "Al-Muddathir", "Al-Qiyamah", "Al-Insan", "Al-Mursalat", "An-Naba",
        "An-Nazi'at", "Abasa", "At-Takwir", "Al-Infitar", "Al-Mutaffifin", "Al-Inshiqaq",
        "Al-Buruj", "At-Tariq", "Al-Ala", "Al-Ghashiyah", "Al-Fajr", "Al-Balad", "Ash-Shams",
        "Al-Lail", "Ad-Duha", "Ash-Sharh", "At-Tin", "Al-Alaq", "Al-Qadr", "Al-Bayyina",
        "Az-Zalzalah", "Al-Adiyat", "Al-Qari'a", "At-Takathur", "Al-Asr", "Al-Humazah",
        "Al-Fil", "Quraysh", "Al-Ma'un", "Al-Kawthar", "Al-Kafirun", "An-Nasr", "Al-Masad",
        "Al-Ikhlas", "Al-Falaq", "An-Nas"
    ]
    return jsonify({'surah_list': surah_list})

# --- Get Prayer Times ---
@app.route('/get-prayer-times')
def get_prayer_times():
    # For now, return sample prayer times
    # In production, you would integrate with a prayer time API
    sample_times = {
        "Fajr": "05:30 AM",
        "Sunrise": "06:45 AM",
        "Dhuhr": "12:30 PM",
        "Asr": "04:00 PM",
        "Maghrib": "06:45 PM",
        "Isha": "08:00 PM"
    }
    return jsonify({'prayer_times': sample_times})

# --- Speech Recognition ---
@app.route('/recognize-speech', methods=['POST'])
def recognize_speech():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file uploaded.'}), 400

    audio_file = request.files['audio']
    temp_path = os.path.join(os.path.dirname(__file__), 'temp_audio.wav')

    try:
        audio_file.save(temp_path)
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

# --- Visual Quran ---
@app.route('/visual-quran')
def visual_quran():
    return render_template('pages/visual_quran.html')

# --- Dua Section ---
@app.route('/duas')
def duas():
    return render_template('pages/duas.html')

# --- Live Meeting route without room_id ---
@app.route('/live-meeting')
def live_meeting_default():
    import uuid
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/live-meeting/{room_id}')


if __name__ == "__main__":
    app.run(debug=True, port=5000)
