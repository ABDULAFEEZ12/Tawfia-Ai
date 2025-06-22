from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, send_file
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

# Initialize Flask app
app = Flask(__name__)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Allow cookies in fetch
app.config['SESSION_COOKIE_SECURE'] = False    # Only True if HTTPS
app.config['SECRET_KEY'] = 'your-secret-key'   # Replace with your secret key
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

# --- Get Questions for User ---
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

# --- Save a Question and Answer ---
def save_question_and_answer(username, question, answer):
    with app.app_context():
        try:
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

        session['user'] = {
            'username': username,
            'email': email,
            'joined_on': new_user.joined_on.strftime('%Y-%m-%d'),
            'preferred_language': 'English',
            'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        }

        flash('Account created successfully!')
        return redirect(url_for('index'))

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
            if request.is_json:
                return jsonify({'success': False, 'error': 'Invalid username or password'}), 401
            else:
                flash('Invalid username or password.')
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
    return render_template('forgot_password.html')  # make sure this template exists

# Set secret key
app.secret_key = os.getenv('MY_SECRET') or 'fallback_secret_key_for_dev_only'

# --- Main index route ---
@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    username = user['username']
    questions = get_questions_for_user(username)
    return render_template('index.html', user=user, questions=questions)

# --- Authentication required decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/my-questions')
@login_required
def my_questions():
    username = session['user']['username']
    questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
    print(f"Fetched questions for {username}: {[q.question for q in questions]}")
    return render_template('my_questions.html', questions=questions)

@app.route('/admin/questions')
def admin_questions():
    questions = UserQuestions.query.all()
    if not questions:
        print("No questions found")
    else:
        for q in questions:
            print(f"{q.username} - {q.question}")
    return render_template('questions.html', questions=questions)

@app.route('/debug/questions')
def debug_questions():
    questions = UserQuestions.query.all()
    return '<br>'.join([f"{q.username}: {q.question}" for q in questions])

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

# Example profile edit
@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        # Save to session or database accordingly
        session['user']['username'] = username
        session['user']['email'] = email
        return redirect(url_for('profile'))
    return render_template('pages/edit_profile.html')

# Additional pages
@app.route('/prayer-times')
def prayer_times():
    return render_template('pages/prayer-times.html')

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
        # ... (rest of reels data)
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
    max_level = max(levels.keys()) if 'levels' in globals() else 20  # fallback max level
    new_level = current_level
    if current_level < max_level:
        new_level = current_level + 1
    session['level'] = new_level
    session['score'] = 0
    session['question_index'] = 0
    session['questions'] = random.sample(get_questions_for_level(new_level), len(get_questions_for_level(new_level)))
    return redirect(url_for('trivia'))

# API endpoints for Quran, Hadith, etc.
@app.route('/api/surah-list')
def surah_list():
    return jsonify([
        {"id": 1, "name": "الفاتحة", "english_name": "Al-Fatihah"},
        {"id": 2, "name": "البقرة", "english_name": "Al-Baqarah"},
        {"id": 3, "name": "آل عمران", "english_name": "Aali Imran"},
        # ... (rest of the list)
    ])

@app.route('/api/surah/<int:surah_id>')
def get_surah_by_id(surah_id):
    # Map surah_id to surah name
    surah_map = {
        1: "Al-Fatihah",
        2: "Al-Baqarah",
        3: "Aali Imran",
        # ... (rest of mapping)
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

@app.route('/daily-dua')
def daily_dua():
    try:
        data_path = os.path.join('DATA', 'daily_duas.json')
        with open(data_path, 'r', encoding='utf-8') as f:
            dua_data = json.load(f)
        if not dua_data or 'duas' not in dua_data:
            return render_template('pages/daily-dua.html', duas=[])
        return render_template('pages/daily-dua.html', duas=dua_data['duas'])
    except Exception as e:
        print(f"Daily Dua Error: {e}")
        return render_template('pages/daily-dua.html', duas=[])

@app.route('/reminder')
def reminder():
    return render_template('pages/reminder.html')

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
    print("📥 Raw incoming data:", data)
    username = session.get('user', {}).get('username')
    history = data.get('history')
    print("🧑 Logged in as:", username)
    print("🧠 History:", history)
    if not username:
        print("❌ User not logged in")
        return jsonify({'error': 'You must be logged in to chat with Tawfiq AI.'}), 401
    if not history:
        print("❌ Missing history")
        return jsonify({'error': 'Chat history is required.'}), 400
    tawfiq_ai_prompt = {
        "role": "system",
        "content": (
            "🌙 You are **Tawfiq AI** — a wise, kind, and emotionally intelligent Muslim assistant created by Tella Abdul Afeez Adewale.\n\n"
            # ... (rest of the prompt)
            "You’re not just smart — you’re **Tawfiq**, the halal AI companion. 💫"
        )
    }
    messages = [tawfiq_ai_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
    if cache_key in question_cache:
        answer = question_cache[cache_key]
        last_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), None)
        if last_question:
            save_question_and_answer(username, last_question, answer)
        return jsonify({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": answer
                }
            }]
        })
    # Prepare API call...
    # (rest of the code remains unchanged)
    # [Omitted here for brevity, but you just keep the existing logic, replacing the route as shown]

# The rest of your routes for /quran-search, /hadith-search, /get-surah-list, etc., remain unchanged, just ensure they are under the same route structure.

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
