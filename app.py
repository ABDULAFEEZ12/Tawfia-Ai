from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import random
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv
import os
from hashlib import sha256
import redis
from functools import wraps
from datetime import datetime

import json
import os

USER_FILE = 'user.json'

# Load users from JSON
def load_users():
    if not os.path.exists(USER_FILE):
        with open(USER_FILE, 'w') as f:
            json.dump({"users": []}, f, indent=2)
    with open(USER_FILE, 'r') as f:
        return json.load(f)

# Save users to JSON
def save_users(data):
    with open(USER_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# Add user if not exists
def add_user(username):
    data = load_users()
    if not any(u['username'] == username for u in data['users']):
        data['users'].append({
            "username": username,
            "questions": []
        })
        save_users(data)
data = {
    'users': []
}
def save_question_and_answer(username, question, answer):
    # Save to JSON (existing code) if needed
    global data
    if 'users' not in data:
        data['users'] = []

    # Save in JSON structure
    user_found = False
    for user in data['users']:
        if user['username'] == username:
            if 'questions' not in user:
                user['questions'] = []
            user['questions'].append({'question': question, 'answer': answer})
            user_found = True
            break
    if not user_found:
        data['users'].append({
            'username': username,
            'questions': [{'question': question, 'answer': answer}],
        })

    # Save in database
    existing_entry = UserQuestions.query.filter_by(username=username, question=question).first()
    if existing_entry:
        # Optional: update the answer if needed
        existing_entry.answer = answer
        existing_entry.timestamp = datetime.utcnow()
    else:
        new_entry = UserQuestions(username=username, question=question, answer=answer)
        db.session.add(new_entry)
    db.session.commit()

user_data = {}

users = {}  # username -> password

from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)

class UserQuestions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

import sqlite3

def get_user_from_db(username):
    conn = sqlite3.connect('your_database.db')
    conn.row_factory = sqlite3.Row  # <-- makes rows behave like dictionaries
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_current_user():
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None

# Load environment variables
load_dotenv()

openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

if not openrouter_api_key:
    raise RuntimeError("OPENROUTER_API_KEY environment variable not set.")

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

app = Flask(__name__)

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.secret_key = 'super_secret_key'  # Replace in production
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, default=1)  # For trivia, later features etc.

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

with app.app_context():
    db.create_all()

    from flask import flash

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']

        if username in users:
            return "Username already exists", 400

        users[username] = {
            'password': password,
            'email': email,
            'joined_on': datetime.now().strftime('%Y-%m-%d'),
            'preferred_language': 'English',
            'last_login': 'N/A'
        }

        save_users(users)  # Fixed: Pass the users dictionary
        return redirect(url_for('index'))

    return render_template('signup.html', user=session.get('user'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = users.get(username)
        if user and user['password'] == password:
            last_login = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            user['last_login'] = last_login
            save_users(users)  # Fixed: Pass the users dictionary

            # Save all user info into one session dictionary
            session['user'] = {
                'username': username,
                'email': user.get('email', f'{username}@example.com'),
                'joined_on': user.get('joined_on', '2023-01-01'),
                'preferred_language': user.get('preferred_language', 'English'),
                'last_login': last_login
            }

            return redirect(url_for('index'))
        else:
            return "Invalid username or password", 401

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

# Read the secret key from environment variable
app.secret_key = os.getenv('MY_SECRET')

# Optional: if the environment variable is not set, use a fallback (not recommended for production)
if not app.secret_key:
    app.secret_key = 'fallback_secret_key_for_dev_only'

questions = levels = {
    1: [
        {
            "question": "Who was the first prophet in Islam?",
            "options": ["Prophet Musa", "Prophet Isa", "Prophet Adam", "Prophet Muhammad"],
            "answer": "Prophet Adam"
        },
        {
            "question": "How many times do Muslims pray in a day?",
            "options": ["3", "5", "7", "6"],
            "answer": "5"
        },
        {
            "question": "Which angel delivered the Quran to the Prophet ﷺ?",
            "options": ["Jibril", "Mikail", "Israfil", "Azrael"],
            "answer": "Jibril"
        },
        {
            "question": "Which month is Ramadan in the Islamic calendar?",
            "options": ["9th", "1st", "12th", "10th"],
            "answer": "9th"
        },
        {
            "question": "What is the first word revealed in the Quran?",
            "options": ["Read", "Pray", "Worship", "Write"],
            "answer": "Read"
        }
    ],

    2: [
        {
            "question": "How many Surahs are in the Quran?",
            "options": ["114", "113", "112", "110"],
            "answer": "114"
        },
        {
            "question": "Which prophet built the Kaaba with his son?",
            "options": ["Ibrahim", "Musa", "Isa", "Nuh"],
            "answer": "Ibrahim"
        },
        {
            "question": "What does 'Salah' mean?",
            "options": ["Charity", "Fasting", "Prayer", "Pilgrimage"],
            "answer": "Prayer"
        },
        {
            "question": "Which prayer is offered just after sunset?",
            "options": ["Fajr", "Maghrib", "Asr", "Isha"],
            "answer": "Maghrib"
        },
        {
            "question": "What is the Islamic month of fasting?",
            "options": ["Shawwal", "Rajab", "Ramadan", "Dhul Hijjah"],
            "answer": "Ramadan"
        }
    ],

    3: [
        {
            "question": "Who was the last prophet in Islam?",
            "options": ["Prophet Isa", "Prophet Musa", "Prophet Muhammad", "Prophet Nuh"],
            "answer": "Prophet Muhammad"
        },
        {
            "question": "What is the Arabic term for charity?",
            "options": ["Zakat", "Sadaqah", "Siyam", "Hajj"],
            "answer": "Sadaqah"
        },
        {
            "question": "Which city is known as the birthplace of Islam?",
            "options": ["Madina", "Mecca", "Jerusalem", "Karbala"],
            "answer": "Mecca"
        },
        {
            "question": "What is the significance of the night of Laylat al-Qadr?",
            "options": ["The night of the Prophet's birth", "The night the Quran was revealed", "The night of the Hijra", "The night of Eid"],
            "answer": "The night the Quran was revealed"
        },
        {
            "question": "Which prophet is known for building the Ark?",
            "options": ["Nuh", "Ibrahim", "Musa", "Yusuf"],
            "answer": "Nuh"
        }
    ],

    4: [
        {
            "question": "What is the direction Muslims face during prayer?",
            "options": ["North", "South", "Qibla (Kaaba)", "East"],
            "answer": "Qibla (Kaaba)"
        },
        {
            "question": "Which surah is known as 'The Heart of the Quran'?",
            "options": ["Al-Fatiha", "Yasin", "Al-Baqarah", "Al-Ikhlas"],
            "answer": "Yasin"
        },
        {
            "question": "How many pillars of Islam are there?",
            "options": ["3", "5", "4", "6"],
            "answer": "5"
        },
        {
            "question": "What is the Islamic declaration of faith called?",
            "options": ["Salah", "Shahada", "Zakat", "Hajj"],
            "answer": "Shahada"
        },
        {
            "question": "Who was the first caliph after Prophet Muhammad?",
            "options": ["Abu Bakr", "Umar", "Uthman", "Ali"],
            "answer": "Abu Bakr"
        }
    ],

    5: [
        {
            "question": "Which angel blows the trumpet to announce the Day of Judgment?",
            "options": ["Jibril", "Israfil", "Mikail", "Azrael"],
            "answer": "Israfil"
        },
        {
            "question": "What is the pilgrimage to Mecca called?",
            "options": ["Hajj", "Umrah", "Ziyarat", "Siyam"],
            "answer": "Hajj"
        },
        {
            "question": "Which surah is the longest in the Quran?",
            "options": ["Al-Baqarah", "Al-Fatiha", "Al-Imran", "An-Nisa"],
            "answer": "Al-Baqarah"
        },
        {
            "question": "What is the Islamic month after Ramadan?",
            "options": ["Shawwal", "Dhul Hijjah", "Dhu al-Qi'dah", "Rajab"],
            "answer": "Shawwal"
        },
        {
            "question": "Who was known as the 'Second Muhammad' due to his knowledge?",
            "options": ["Imam Abu Hanifa", "Imam Malik", "Imam Shafi'i", "Imam Ahmad"],
            "answer": "Imam Ahmad"
        }
    ],

    6: [
        {
            "question": "What is the term for the Islamic law?",
            "options": ["Sharia", "Fiqh", "Sunnah", "Hadd"],
            "answer": "Sharia"
        },
        {
            "question": "Which Prophet is associated with the story of Yusuf (Joseph)?",
            "options": ["Yusuf", "Musa", "Isa", "Nuh"],
            "answer": "Yusuf"
        },
        {
            "question": "In which city did the Prophet Muhammad pass away?",
            "options": ["Mecca", "Madina", "Karbala", "Jerusalem"],
            "answer": "Madina"
        },
        {
            "question": "What is the name of the Islamic prayer performed at dawn?",
            "options": ["Fajr", "Dhuhr", "Asr", "Isha"],
            "answer": "Fajr"
        },
        {
            "question": "Which prophet is known for splitting the Red Sea?",
            "options": ["Musa", "Isa", "Yusuf", "Nuh"],
            "answer": "Musa"
        }
    ],

    7: [
        {
            "question": "What does the term 'Halal' mean?",
            "options": ["Forbidden", "Permissible", "Unclean", "Sacred"],
            "answer": "Permissible"
        },
        {
            "question": "Which city is called the 'City of the Prophet'?",
            "options": ["Mecca", "Madina", "Jerusalem", "Cairo"],
            "answer": "Madina"
        },
        {
            "question": "What is the name of the festival that marks the end of Ramadan?",
            "options": ["Eid al-Fitr", "Eid al-Adha", "Lailat al-Qadr", "Mawlid"],
            "answer": "Eid al-Fitr"
        },
        {
            "question": "Which prophet is associated with building the Ark?",
            "options": ["Nuh", "Ibrahim", "Musa", "Yusuf"],
            "answer": "Nuh"
        },
        {
            "question": "What is the Islamic term for the fast of Ramadan?",
            "options": ["Siyam", "Hajj", "Zakat", "Qiyam"],
            "answer": "Siyam"
        }
    ],

    8: [
        {
            "question": "Which Surah is known as 'The Opening'?",
            "options": ["Al-Fatiha", "Al-Baqarah", "Al-Ikhlas", "Al-Nas"],
            "answer": "Al-Fatiha"
        },
        {
            "question": "Who was the wife of the Prophet Muhammad?",
            "options": ["Aisha", "Khadijah", "Fatimah", "Hafsa"],
            "answer": "Khadijah"
        },
        {
            "question": "What is the name of the Islamic prayer performed after sunset?",
            "options": ["Maghrib", "Fajr", "Isha", "Dhuhr"],
            "answer": "Maghrib"
        },
        {
            "question": "Which prophet is associated with the story of the cow?",
            "options": ["Yusuf", "Musa", "Isa", "Nuh"],
            "answer": "Musa"
        },
        {
            "question": "What is the significance of the month of Dhu al-Hijjah?",
            "options": ["Hajj pilgrimage", "Fasting", "New Year", "Eid"],
            "answer": "Hajj pilgrimage"
        }
    ],

    9: [
        {
            "question": "What is the Arabic term for 'Good Deeds'?",
            "options": ["Amal", "A'mal", "A'mal Salih", "Sadaqah"],
            "answer": "A'mal Salih"
        },
        {
            "question": "Which prophet is known for his patience and long life?",
            "options": ["Nuh", "Ayyub", "Ibrahim", "Yusuf"],
            "answer": "Ayyub"
        },
        {
            "question": "What is the name of the city where the Prophet Muhammad was born?",
            "options": ["Mecca", "Madina", "Jerusalem", "Cairo"],
            "answer": "Mecca"
        },
        {
            "question": "Which of the following is NOT one of the five pillars of Islam?",
            "options": ["Salah", "Zakat", "Hajj", "Jihad"],
            "answer": "Jihad"
        },
        {
            "question": "What is the Islamic term for the pilgrimage to Mecca?",
            "options": ["Hajj", "Umrah", "Ziyarat", "Siyam"],
            "answer": "Hajj"
        }
    ],

    10: [
        {
            "question": "Who was the first martyr in Islam?",
            "options": ["Sumayyah", "Bilal", "Khadijah", "Umar"],
            "answer": "Sumayyah"
        },
        {
            "question": "What is the name of the mountain where Prophet Musa received the commandments?",
            "options": ["Mount Sinai", "Mount Arafat", "Mount Everest", "Mount Thawr"],
            "answer": "Mount Sinai"
        },
        {
            "question": "Which Surah is known as 'The Women'?",
            "options": ["An-Nisa", "Al-Mumtahanah", "Al-Mumtahinah", "Al-Ma'idah"],
            "answer": "An-Nisa"
        },
        {
            "question": "What does the term 'Eid' mean?",
            "options": ["Festival", "Fast", "Prayer", "Pilgrimage"],
            "answer": "Festival"
        },
        {
            "question": "Which prophet is associated with the story of the whale?",
            "options": ["Yunus", "Musa", "Isa", "Nuh"],
            "answer": "Yunus"
        }
    ],

    11: [
        {
            "question": "What is the Islamic ruling called for giving a portion of wealth to the poor?",
            "options": ["Zakat", "Sadaqah", "Fitrah", "Kaffara"],
            "answer": "Zakat"
        },
        {
            "question": "Which prophet is known for his wisdom and the story of the two women and the baby?",
            "options": ["Sulaiman", "Yusuf", "Ibrahim", "Musa"],
            "answer": "Sulaiman"
        },
        {
            "question": "What is the name of the night when the Quran was first revealed?",
            "options": ["Laylat al-Qadr", "Eid al-Fitr", "Laylat al-Miraj", "Laylat al-Bara'ah"],
            "answer": "Laylat al-Qadr"
        },
        {
            "question": "Which city did the Prophet migrate to from Mecca?",
            "options": ["Madina", "Jerusalem", "Cairo", "Baghdad"],
            "answer": "Madina"
        },
        {
            "question": "What is the Islamic term for the act of fasting during Ramadan?",
            "options": ["Siyam", "Zakat", "Hajj", "Qiyam"],
            "answer": "Siyam"
        }
    ],

    12: [
        {
            "question": "Which surah is known as 'The Chapter of Light'?",
            "options": ["An-Nur", "Al-Hadid", "Al-Ma'idah", "Al-Anfal"],
            "answer": "An-Nur"
        },
        {
            "question": "Who was the mother of Prophet Isa (Jesus)?",
            "options": ["Maryam", "Khadijah", "Asiya", "Hawwa"],
            "answer": "Maryam"
        },
        {
            "question": "What is the name of the Islamic prayer performed at midday?",
            "options": ["Dhuhr", "Asr", "Fajr", "Isha"],
            "answer": "Dhuhr"
        },
        {
            "question": "Which prophet is associated with the story of the flood?",
            "options": ["Nuh", "Musa", "Yusuf", "Ibrahim"],
            "answer": "Nuh"
        },
        {
            "question": "What is the Islamic term for the pilgrimage to Mecca during Hajj?",
            "options": ["Tawaf", "Sa'i", "Ihram", "Hajj"],
            "answer": "Hajj"
        }
    ],

    13: [
        {
            "question": "What is the name of the angel responsible for taking souls?",
            "options": ["Mikail", "Jibril", "Azrael", "Israfil"],
            "answer": "Azrael"
        },
        {
            "question": "Which Surah is known as 'The Opening'?",
            "options": ["Al-Fatiha", "Al-Baqarah", "Al-Ikhlas", "Al-Nas"],
            "answer": "Al-Fatiha"
        },
        {
            "question": "Who was the first person to accept Islam after the Prophet?",
            "options": ["Khadijah", "Ali", "Abu Bakr", "Umar"],
            "answer": "Khadijah"
        },
        {
            "question": "Which prophet is called the 'Friend of Allah'?",
            "options": ["Ibrahim", "Yusuf", "Musa", "Isa"],
            "answer": "Ibrahim"
        },
        {
            "question": "What is the term for the minor pilgrimage in Islam?",
            "options": ["Umrah", "Hajj", "Ziyarat", "Siyam"],
            "answer": "Umrah"
        }
    ],

    14: [
        {
            "question": "Which city is the third holiest city in Islam?",
            "options": ["Jerusalem", "Mecca", "Madina", "Karbala"],
            "answer": "Jerusalem"
        },
        {
            "question": "What is the name of the Islamic prayer performed in the evening?",
            "options": ["Isha", "Maghrib", "Fajr", "Dhuhr"],
            "answer": "Isha"
        },
        {
            "question": "Who was the first martyr in Islam?",
            "options": ["Sumayyah", "Bilal", "Khadijah", "Umar"],
            "answer": "Sumayyah"
        },
        {
            "question": "Which prophet is known for his patience during suffering?",
            "options": ["Ayyub", "Yunus", "Ibrahim", "Nuh"],
            "answer": "Ayyub"
        },
        {
            "question": "What does the term 'Hijrah' refer to?",
            "options": ["Migration", "Fasting", "Prayer", "Pilgrimage"],
            "answer": "Migration"
        }
    ],

    15: [
        {
            "question": "What is the significance of the month of Muharram?",
            "options": ["New Year", "Day of Arafah", "Eid al-Adha", "Ashura"],
            "answer": "Ashura"
        },
        {
            "question": "Which prophet is associated with the story of the two gardens?",
            "options": ["Yusuf", "Dhul-Qarnayn", "Sulaiman", "Yunus"],
            "answer": "Yusuf"
        },
        {
            "question": "What is the name of the chapter that describes the Battle of Badr?",
            "options": ["Al-Anfal", "Ali-Imran", "Al-Mumtahanah", "Al-Hajj"],
            "answer": "Al-Anfal"
        },
        {
            "question": "Who is known as the 'Second Caliph'?",
            "options": ["Umar ibn al-Khattab", "Uthman ibn Affan", "Ali ibn Abi Talib", "Abu Bakr"],
            "answer": "Umar ibn al-Khattab"
        },
        {
            "question": "What is the Islamic ruling on interest (riba)?",
            "options": ["Permissible", "Forbidden", "Must be paid", "Optional"],
            "answer": "Forbidden"
        }
    ],

    16: [
        {
            "question": "What is the name of the mountain where Prophet Musa received the commandments?",
            "options": ["Mount Sinai", "Mount Arafat", "Mount Thawr", "Mount Everest"],
            "answer": "Mount Sinai"
        },
        {
            "question": "What is the name of the compulsory charity paid during Ramadan?",
            "options": ["Zakat al-Fitr", "Zakat al-Mal", "Sadaqah", "Fitrah"],
            "answer": "Zakat al-Fitr"
        },
        {
            "question": "Which Surah is known as 'The Cow'?",
            "options": ["Al-Baqarah", "Al-Imran", "An-Nisa", "Al-Ma'idah"],
            "answer": "Al-Baqarah"
        },
        {
            "question": "Who was the Prophet's first wife?",
            "options": ["Khadijah", "Aisha", "Hafsa", "Zaynab"],
            "answer": "Khadijah"
        },
        {
            "question": "What does the term 'Makkah' mean?",
            "options": ["City of the Prophet", "The Sacred Mosque", "The Holy City", "The City of Mecca"],
            "answer": "The City of Mecca"
        }
    ],

    17: [
        {
            "question": "Which prophet is associated with the story of the burning bush?",
            "options": ["Musa", "Isa", "Yusuf", "Nuh"],
            "answer": "Musa"
        },
        {
            "question": "What is the name of the festival that commemorates Prophet Ibrahim's willingness to sacrifice his son?",
            "options": ["Eid al-Adha", "Eid al-Fitr", "Lailat al-Qadr", "Mawlid"],
            "answer": "Eid al-Adha"
        },
        {
            "question": "Which chapter of the Quran is known as 'The Family of Imran'?",
            "options": ["Al-Imran", "Al-Anfal", "Al-Ma'idah", "Al-Hadid"],
            "answer": "Al-Imran"
        },
        {
            "question": "What is the term for the Islamic study of law and jurisprudence?",
            "options": ["Fiqh", "Tafsir", "Hadith", "Aqidah"],
            "answer": "Fiqh"
        },
        {
            "question": "Who was the Prophet's uncle who supported him in Mecca?",
            "options": ["Abu Talib", "Umar", "Uthman", "Ali"],
            "answer": "Abu Talib"
        }
    ],

    18: [
        {
            "question": "What is the name of the city where the Prophet performed the Night Journey (Isra and Miraj)?",
            "options": ["Jerusalem", "Mecca", "Madina", "Cairo"],
            "answer": "Jerusalem"
        },
        {
            "question": "Which Surah is called 'The Light'?",
            "options": ["An-Nur", "Al-Hadid", "Al-Mumtahanah", "Al-Ahzab"],
            "answer": "An-Nur"
        },
        {
            "question": "What is the term for the Islamic obligation to give a specific portion of wealth to the needy?",
            "options": ["Zakat", "Sadaqah", "Fitrah", "Kaffara"],
            "answer": "Zakat"
        },
        {
            "question": "Who is the Prophet associated with the story of the two sons and the murder?",
            "options": ["Qabil and Habil", "Yusuf and Benjamin", "Isa and Yahya", "Musa and Harun"],
            "answer": "Qabil and Habil"
        },
        {
            "question": "What is the name of the Islamic prayer performed at night?",
            "options": ["Qiyam", "Taraweeh", "Tahajjud", "Isha"],
            "answer": "Tahajjud"
        }
    ],

    19: [
        {
            "question": "Which chapter of the Quran discusses the creation of man?",
            "options": ["Al-Mu'minun", "Al-Hajj", "Al-Alaq", "Al-Qiyamah"],
            "answer": "Al-Alaq"
        },
        {
            "question": "What is the name of the festival that celebrates the birthday of Prophet Muhammad?",
            "options": ["Mawlid", "Eid al-Fitr", "Eid al-Adha", "Lailat al-Qadr"],
            "answer": "Mawlid"
        },
        {
            "question": "Which prophet is called the 'Friend of Allah' in Islamic tradition?",
            "options": ["Ibrahim", "Musa", "Isa", "Yusuf"],
            "answer": "Ibrahim"
        },
        {
            "question": "What is the Arabic term for the Day of Judgment?",
            "options": ["Qiyamah", "Salah", "Jannah", "Akhirah"],
            "answer": "Qiyamah"
        },
        {
            "question": "Which prophet was sent to the people of Nineveh?",
            "options": ["Yunus", "Nuh", "Ibrahim", "Musa"],
            "answer": "Yunus"
        }
    ],

    20: [
        {
            "question": "What is the name of the event where the Prophet ascended to the heavens?",
            "options": ["Isra and Miraj", "Hajj", "Laylat al-Qadr", "Mawlid"],
            "answer": "Isra and Miraj"
        },
        {
            "question": "Which surah is often recited for protection and is called 'The Queen'?",
            "options": ["Al-Naml", "Al-Fil", "Al-Mulk", "Al-Hadid"],
            "answer": "Al-Mulk"
        },
        {
            "question": "Who was the first person to accept Islam from the youth?",
            "options": ["Ali ibn Abi Talib", "Umar", "Abu Bakr", "Bilal"],
            "answer": "Ali ibn Abi Talib"
        },
        {
            "question": "Which city was the first capital of the Islamic Caliphate?",
            "options": ["Medina", "Kufa", "Damascus", "Baghdad"],
            "answer": "Kufa"
        },
        {
            "question": "What is the term for the Islamic concept of divine decree and predestination?",
            "options": ["Qadar", "Tawakkul", "Tawhid", "Aqidah"],
            "answer": "Qadar"
        }
    ],
}

def get_questions_for_level(level):
    return levels.get(level, [])



@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', user=session.get('user'))

from functools import wraps

# Add this login_required decorator (place it with your other utility functions)
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
    return render_template('my_questions.html', questions=questions)

@app.route('/profile')
@login_required
def profile():
    user = session.get('user', {})  # Get the user dictionary or an empty one

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

        # Save data to dictionary (or database later)
        user_data['username'] = username
        user_data['email'] = email

        # Redirect to profile page after update
        return redirect(url_for('profile'))

    return render_template('pages/edit_profile.html')

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
        # Advance to next level only if passed
        session['level'] = level + 1

    # Reset score and question index whether passed or not
    session['score'] = 0
    session['question_index'] = 0

    return render_template('result.html', score=score, total=total, passed=passed, level=level)


@app.route('/restart')
def restart():
    # Do NOT clear level; just reset current level's questions
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
        # ... up to 114
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
    # Your dashboard logic here
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
    # About page - in templates/pages/about.html
    return render_template('pages/about.html')

@app.route('/feedback')
def feedback():
    return render_template('pages/feedback.html')


# --- Ask API endpoint ---
from flask import request, jsonify
from hashlib import sha256
import json
import requests

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    history = data.get('history', [])
    username = data.get('username')

    if not username or not history:
        return jsonify({'error': 'Username and history are required'}), 400

    system_prompt = {
        "role": "system",
        "content": (
            # Your full system prompt text here
            "You are Tawfiq AI — a wise, kind, and lovable Muslim assistant created by Tella Abdul Afeez Adewale..."
            # [TRUNCATED for brevity]
        )
    }

    messages = [system_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    # Check cache logic remains the same
    if cache_key in question_cache:
        answer = question_cache[cache_key]
        last_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), None)
        if last_question:
            save_question_and_answer(username, last_question, answer)
        # Return in the new format
        return jsonify({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": answer
                    }
                }
            ]
        })

    # Call OpenRouter API
    openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post(openrouter_api_url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        print("🔍 OpenRouter Result:", json.dumps(result, indent=2))  # Debug

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        if not answer:
            answer = "I'm sorry, I couldn't generate a response. Please try again later."

        banned_phrases = [
            "i don't have a religion",
            "as an ai developed by",
            "i can't say one religion is best",
            "i am neutral",
            "as an ai language model",
            "developed by openai",
            "my creators at openai"
        ]

        if any(phrase in answer.lower() for phrase in banned_phrases):
            answer = (
                "I was created by Tella Abdul Afeez Adewale to serve the Ummah with wisdom and knowledge. "
                "Islam is the final and complete guidance from Allah through Prophet Muhammad (peace be upon him). "
                "I’m always here to assist you with Islamic and helpful answers."
            )

        # Save to cache
        question_cache[cache_key] = answer
        save_cache()

        # Save user Q&A
        last_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), None)
        if last_question:
            save_question_and_answer(username, last_question, answer)

        # Return in the new format for frontend
        return jsonify({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": answer
                    }
                }
            ]
        })

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({'choices': [{'message': {'role': 'assistant', 'content': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'}}]})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'choices': [{'message': {'role': 'assistant', 'content': 'An unexpected error occurred. Please try again later.'}}]})
# --- Quran Search with local data fallback ---
@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    # Path to local surah data
    local_surah_path = os.path.join('DATA', 'surah.json')

    surahs = []

    # Try to load local surah data first
    if os.path.exists(local_surah_path):
        try:
            with open(local_surah_path, 'r', encoding='utf-8') as f:
                surahs = json.load(f)
            print("Loaded surah data from local file.")
        except json.JSONDecodeError:
            print("Error decoding local surah data. Will fetch from API.")
        except Exception as e:
            print(f"Unexpected error loading local surah data: {e}")

    # If local data not loaded, fetch from API
    if not surahs:
        try:
            response = requests.get('https://api.quran.gading.dev/surah')
            response.raise_for_status()
            surahs = response.json().get('data', [])
            # Save to local file for future use
            try:
                with open(local_surah_path, 'w', encoding='utf-8') as f:
                    json.dump(surahs, f, indent=2, ensure_ascii=False)
                print("Saved surah data to local file.")
            except Exception as e:
                print(f"Error saving surah data locally: {e}")
        except requests.RequestException as e:
            print(f"Quran API Error: {e}")
            return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

    # Map surah names to numbers
    surah_names = {s['name']['transliteration']['en'].lower(): s['number'] for s in surahs}
    close_matches = get_close_matches(query, surah_names.keys(), n=1, cutoff=0.6)

    if close_matches:
        surah_number = surah_names[close_matches[0]]
        try:
            verses_response = requests.get(f'https://api.quran.gading.dev/surah/{surah_number}')
            verses_response.raise_for_status()
            surah_data = verses_response.json().get('data', [])

            surah_title = f"{surah_data['name']['transliteration']['en']} ({surah_data['name']['short']})"
            structured_verses = [{
                'surah_name': surah_data['name']['transliteration']['en'],
                'surah_number': surah_number,
                'verse_number': v['number']['inSurah'],
                'translation': v['translation']['en'],
                'arabic_text': v['text']['arab']
            } for v in surah_data['verses']]

            return jsonify({'surah_title': surah_title, 'results': structured_verses})
        except requests.RequestException as e:
            print(f"Error fetching verses: {e}")
            return jsonify({'result': 'Error fetching verses. Try again later.', 'results': []})
    else:
        return jsonify({'result': f'No Surah found for "{query}".', 'results': []})

# --- Hadith Search ---
@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.', 'results': []})

    # Normalize query
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
@app.route('/get-surah-list')
def get_surah_list():
    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json().get('data', [])
        names = [s['name']['transliteration']['en'] for s in surahs]
        return jsonify({'surah_list': names})
    except requests.RequestException as e:
        print(f"Surah List API Error: {e}")
        return jsonify({'surah_list': []})

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

# --- Speech Recognition ---
@app.route('/recognize-speech', methods=['POST'])
def recognize_speech():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file uploaded.'}), 400

    audio_file = request.files['audio']
    temp_path = os.path.join(os.path.dirname(__file__), 'temp_audio.wav')

    try:
        # Save uploaded audio temporarily
        audio_file.save(temp_path)

        # Recognize speech
        import speech_recognition as sr
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
    
