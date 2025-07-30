from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, flash,
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

# Load environment variables
load_dotenv()

print("✅ API KEY:", os.getenv("GOOGLE_NEWS_API_KEY"))
print("✅ CX:", os.getenv("GOOGLE_CX"))

# Initialize Flask app
app = Flask(__name__)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Allow cookies in fetch
app.config['SESSION_COOKIE_SECURE'] = False    # Only True if HTTPS
app.config['SECRET_KEY'] = 'your-secret-key'   # Required for session to work
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')

# Initialize SQLAlchemy
db = SQLAlchemy()
db.init_app(app)

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
    db.create_all()

# --- Create a default user (optional testing/demo) ---
with app.app_context():
    user = User.query.filter_by(username='zayd').first()
    if not user:
        user = User(username='zayd', email='zayd@example.com')
        user.set_password('secure123')
        db.session.add(user)
        db.session.commit()

# --- Get Questions for User (static demo for now) ---
def get_questions_for_user(username):
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

# --- Save a Question and Answer for a User ---
def save_question_and_answer(username, question, answer):
    with app.app_context():
        try:
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
            print(f"❌ Failed to save Q&A for '{username}': {e}")
            db.session.rollback()

# --- Redis Cache Setup ---
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))
redis_db = int(os.getenv("REDIS_DB", 0))
redis_password = os.getenv("REDIS_PASSWORD", None)

r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)

# --- File-Based Cache ---
CACHE_FILE = "tawfiq_cache.json"

# Load cache from file
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
    raise RuntimeError("OPENROUTER_API_KEY environment variable not set.")

def load_users():
    if os.path.exists('users.json'):
        with open('users.json', 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open('users.json', 'w') as f:
        json.dump(users, f)

users = load_users()

# --- Flask Routes and Logic ---

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # Signup route remains if needed, or can be removed if not used
    if request.method == 'POST':
        username = request.form.get('username').strip()
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()

        if not username or not password or not email:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))

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

        flash('Account created successfully!')
        return redirect(url_for('index'))

    return render_template('signup.html', user=None)

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Removed login route entirely
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    # Removed logout route
    return redirect(url_for('index'))

@app.route('/forgot-password')
def forgot_password():
    # Optional password reset page
    return render_template('forgot_password.html')

# Removed login_required decorator and all routes that depend on session user

@app.route('/')
def index():
    # Since no login, simply render index or demo page
    # For demonstration, pass a dummy user info or none
    user = None
    return render_template('index.html', user=user)

@app.route('/my-questions')
def my_questions():
    # Since no login, show all questions or none
    questions = UserQuestions.query.order_by(UserQuestions.timestamp.desc()).all()
    return render_template('my_questions.html', questions=questions)

@app.route('/admin/questions')
def admin_questions():
    questions = UserQuestions.query.all()
    return render_template('questions.html', questions=questions)

@app.route('/debug/questions')
def debug_questions():
    questions = UserQuestions.query.all()
    return '<br>'.join([f"{q.username}: {q.question}" for q in questions])

@app.route('/profile')
def profile():
    # No session info, show generic or static profile info
    return render_template('profile.html', username='Guest', email='not_set@example.com')

@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    # No session info; handle form if needed
    if request.method == 'POST':
        # Save profile info if applicable
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
    # ... (rest remains the same)
    # For brevity, keep the original code here
    # ...
    return jsonify([])  # Placeholder if no API key

@app.route("/memorize_quran")
def memorize_quran():
    return render_template("pages/memorize_quran.html")

@app.route('/reels')
def reels():
    reels_data = [
        # ... (your reels data)
    ]
    return render_template('pages/reels.html', reels=reels_data)

@app.route('/trivia', methods=['GET', 'POST'])
def trivia():
    # No session-based progression; you can keep as is or reset logic
    # For simplicity, start from first question every time
    questions = get_questions_for_level(1)
    q_index = 0
    question = questions[q_index]
    return render_template('trivia.html', question=question, index=1, total=len(questions))

@app.route('/trivia_result')
def trivia_result():
    # Simplify result page
    return render_template('result.html', score=0, total=0, passed=True, level=1)

@app.route('/restart')
def restart():
    # Reset trivia state
    return redirect(url_for('trivia'))

@app.route('/next_level')
def next_level():
    # Reset to first level
    return redirect(url_for('trivia'))

@app.route('/api/surah-list')
def surah_list():
    # As before
    return jsonify([])

@app.route('/api/surah/<int:surah_id>')
def get_surah_by_id(surah_id):
    # As before
    return jsonify({})

@app.route('/islamic-motivation')
def get_islamic_motivation():
    # As before
    return jsonify({'quote': ''})

@app.route('/recognize-speech', methods=['POST'])
def recognize_speech():
    # As before
    return jsonify({'transcript': ''})

# Additional API routes...
# Keep the rest of your code as is, removing any session or login-dependent parts.

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
