from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash
)
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import json
import requests
from bs4 import BeautifulSoup
from hashlib import sha256
import openai

# Load environment variables
load_dotenv()

# ----------------------
# Flask + DB Setup
# ----------------------
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ----------------------
# OpenAI / OpenRouter API
# ----------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

# ----------------------
# Models (Keeping for question storage, but removing user auth)
# ----------------------
class UserQuestions(db.Model):
    __tablename__ = 'user_questions'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# ----------------------
# Helpers (Removed login_required decorator)
# ----------------------
def save_question_and_answer(username, question, answer):
    entry = UserQuestions(username=username, question=question, answer=answer)
    db.session.add(entry)
    db.session.commit()

CACHE_FILE = "tellavista_cache.json"
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

# ----------------------
# Routes
# ----------------------
@app.route('/')
def index():
    # Set a default guest user if not logged in
    if 'user' not in session:
        session['user'] = {'username': 'Guest', 'email': 'guest@example.com'}
    return render_template('index.html')

# -------- REMOVED: SIGNUP, LOGIN, LOGOUT ROUTES --------

# -------- MY QUESTIONS (Now accessible to all) --------
@app.route('/my-questions')
def my_questions():
    username = session['user']['username']
    questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
    return render_template('my_questions.html', questions=questions)

# -------- PROFILE (Simplified for guest users) --------
@app.route('/profile')
def profile():
    user = session.get('user', {})
    return render_template('profile.html', user=user)

@app.route('/talk-to-tellavista', methods=['GET', 'POST'])
def talk_to_tellavista():
    if request.method == 'GET':
        return render_template('talk_to_tellavista.html')

    try:
        data = request.get_json()
        history = data.get('history', [])
        username = session.get('user', {}).get('username', 'Guest')

        reply = f"Hello {username}, I'm Telavista! How can I assist you today?"

        return jsonify({
            "choices": [
                {"message": {"content": reply}}
            ]
        })
    except Exception as e:
        return jsonify({
            "choices": [
                {"message": {"content": f"❌ Error: {str(e)}"}}
            ]
        })

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/memory', methods=['POST'])
def save_memory():
    session['theme'] = request.form.get('theme')
    session['language'] = request.form.get('language')
    session['notifications'] = 'notifications' in request.form
    return redirect('/settings')

@app.route('/telavista/memory', methods=['POST'])
def telavista_save_memory():
    print("Saving Telavista memory!")
    return redirect('/settings')

# ------------------ MATERIALS PAGE ------------------
@app.route('/materials')
def materials():
    all_courses = ["Python", "Data Science", "AI Basics", "Math", "Physics"]
    selected_course = request.args.get("course")
    materials = []

    if selected_course:
        materials = [
            {
                "title": f"{selected_course} Introduction",
                "description": f"Basics of {selected_course}",
                "link": "https://youtube.com"
            },
            {
                "title": f"{selected_course} Tutorial",
                "description": f"Complete guide on {selected_course}",
                "link": "https://youtube.com"
            }
        ]

    return render_template(
        "materials.html",
        courses=all_courses,
        selected_course=selected_course,
        materials=materials
    )


# ------------------ API: BASIC MATERIALS ONLY ------------------
@app.route('/api/materials')
def get_study_materials():
    query = request.args.get("q", "python")

    # 1. Fetch PDFs from PDFDrive
    pdfs = []
    try:
        pdf_html = requests.get(f"https://www.pdfdrive.com/search?q={query}", headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:5]:
            title = book.select_one('img')['alt']
            link = "https://www.pdfdrive.com" + book.parent['href']
            pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    # 2. Fetch Books from Open Library
    books = []
    try:
        ol_data = requests.get(f"https://openlibrary.org/search.json?q={query}").json()
        for doc in ol_data.get("docs", [])[:5]:
            books.append({
                "title": doc.get("title"),
                "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                "link": f"https://openlibrary.org{doc.get('key')}"
            })
    except Exception as e:
        books = [{"error": str(e)}]

    return jsonify({
        "query": query,
        "pdfs": pdfs,
        "books": books
    })

# ------------------ API: AI + MATERIALS ------------------
def is_academic_book(title, topic, department):
    title_lower = title.lower()
    topic_lower = topic.lower()
    department_lower = department.lower()

    academic_keywords = [
        "principles", "fundamentals", "introduction", "basics", "theory",
        "textbook", "manual", "engineering", "mathematics", "analysis",
        "guide", "mechanics", "accounting", "algebra", "economics", "physics",
        "statistics", topic_lower, department_lower
    ]

    fiction_keywords = [
        "novel", "jedi", "star wars", "story", "episode", "adventure", "magic",
        "wizard", "putting", "love", "mystery", "thriller", "detective",
        "vampire", "romance", "oz", "dragon", "ghost", "horror"
    ]

    if any(bad in title_lower for bad in fiction_keywords):
        return False
    if any(good in title_lower for good in academic_keywords):
        return True
    return False

@app.route('/ai/materials')
def ai_materials():
    topic = request.args.get("topic")
    level = request.args.get("level")
    department = request.args.get("department")
    goal = request.args.get("goal")
    
    if not goal:
        goal = "general"

    if not topic or not level or not department:
        return jsonify({"error": "Missing one or more parameters: topic, level, department"}), 400

    # AI Explanation
    prompt = f"""
    You're an educational AI helping a {level} student in the {department} department.
    They want to learn: '{goal}' in the topic of {topic}.
    Provide a short and clear explanation to help them get started.
    End with: '📚 Here are materials to study further:'
    """

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "You're a helpful and knowledgeable tutor."},
                {"role": "user", "content": prompt}
            ]
        )
        explanation = response.choices[0].message.content
    except Exception as e:
        explanation = f"Error generating explanation: {str(e)}"

    # Search PDFDrive
    pdfs = []
    try:
        pdf_html = requests.get(f"https://www.pdfdrive.com/search?q={topic}", headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:10]:
            title = book.select_one('img')['alt']
            if is_academic_book(title, topic, department):
                link = "https://www.pdfdrive.com" + book.parent['href']
                pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    # Search OpenLibrary
    books = []
    try:
        ol_data = requests.get(f"https://openlibrary.org/search.json?q={topic}").json()
        for doc in ol_data.get("docs", [])[:10]:
            title = doc.get("title", "")
            if is_academic_book(title, topic, department):
                books.append({
                    "title": doc.get("title"),
                    "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                    "link": f"https://openlibrary.org{doc.get('key')}"
                })
    except Exception as e:
        books = [{"error": str(e)}]

    if not pdfs and not books:
        return jsonify({
            "query": topic,
            "ai_explanation": explanation,
            "pdfs": [],
            "books": [],
            "message": "❌ No academic study materials found for this topic."
        })

    return jsonify({
        "query": topic,
        "ai_explanation": explanation,
        "pdfs": pdfs,
        "books": books
    })

# ------------------ REELS ------------------
@app.route('/reels', methods=['GET'])
def reels():
    categories = ["Tech", "Motivation", "Islamic", "AI"]
    selected_category = request.args.get("category")
    videos = []

    if selected_category:
        videos = [
            {"title": f"{selected_category} Reel 1", "video_id": "abc123"},
            {"title": f"{selected_category} Reel 2", "video_id": "def456"}
        ]

    return render_template("reels.html",
                           user=session.get("user"),
                           categories=categories,
                           selected_category=selected_category,
                           videos=videos)

@app.route("/api/reels")
def get_reels():
    course = request.args.get("course")

    all_reels = [
        # ... (your existing reels data remains the same)
        {"course": "Accountancy", "caption": "Introduction to Accounting", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=FNnNZBbmBh0yqvrk"},
        {"course": "Accountancy", "caption": "Financial Statements Basics", "video_url": "https://youtu.be/fb7YCVR5fIU?si=XWozkxGoBV2HP2HW"},
        # ... (rest of your reels data)
    ]

    matching = [r for r in all_reels if r["course"] == course]
    return jsonify({"reels": matching})

# ------------------ TRIVIA GAMES ------------------
@app.route('/CBT', methods=['GET'])
def CBT():
    topics = ["Python", "Hadith", "AI", "Math"]
    selected_topic = request.args.get("topic")
    questions = []

    if selected_topic:
        questions = [
            {"question": f"What is {selected_topic}?", "options": ["Option A", "Option B", "Option C"], "answer": "Option A"},
            {"question": f"Why is {selected_topic} important?", "options": ["Reason 1", "Reason 2", "Reason 3"], "answer": "Reason 2"}
        ]
    return render_template("CBT.html", user=session.get("user"), topics=topics, selected_topic=selected_topic, questions=questions)

@app.route('/teach-me-ai')
def teach_me_ai():
    return render_template('teach-me-ai.html')

@app.route('/api/ai-teach')
def ai_teach():
    course = request.args.get("course")
    level = request.args.get("level")

    if not course or not level:
        return jsonify({"error": "Missing course or level"}), 400

    prompt = f"You're a tutor. Teach a {level} student the basics of {course} in a friendly and easy-to-understand way."

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "You are an educational AI assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        return jsonify({"summary": response.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    username = session.get('user', {}).get('username', 'Guest')
    history = data.get('history')

    if not history:
        return jsonify({'error': 'Chat history required'}), 400

    # Extract last user question
    user_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')
    if not user_question:
        return jsonify({'error': 'No user question found'}), 400

    # Fetch Materials
    def get_pdfs(query):
        pdfs = []
        try:
            html = requests.get(
                f"https://www.pdfdrive.com/search?q={query}",
                headers={"User-Agent": "Mozilla/5.0"}
            ).text
            soup = BeautifulSoup(html, 'html.parser')
            for book in soup.select('.file-left')[:3]:
                title = book.select_one('img')['alt']
                link = "https://www.pdfdrive.com" + book.parent['href']
                pdfs.append({'title': title, 'link': link})
        except Exception as e:
            print("PDF fetch error:", e)
        return pdfs

    def get_books(query):
        books = []
        try:
            res = requests.get(f"https://openlibrary.org/search.json?q={query}").json()
            for doc in res.get("docs", [])[:2]:
                books.append({
                    "title": doc.get("title"),
                    "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                    "link": f"https://openlibrary.org{doc.get('key')}"
                })
        except Exception as e:
            print("Book fetch error:", e)
        return books

    pdfs = get_pdfs(user_question)
    books = get_books(user_question)

    # Build study materials text
    materials_text = "📚 Study Materials:\n"
    for pdf in pdfs:
        materials_text += f"- {pdf['title']} ({pdf['link']})\n"
    for book in books:
        materials_text += f"- {book['title']} by {book['author']} ({book['link']})\n"

    # System role
    system_prompt = {
        "role": "system",
        "content": f"You are Tellavista — a wise, kind, and helpful AI tutor. Use these study materials to guide your response:\n\n{materials_text}\n\nAnswer in a clear, educational way as if you're teaching a student from scratch."
    }

    messages = [system_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    # Cache hit
    if cache_key in question_cache:
        answer = question_cache[cache_key]
        save_question_and_answer(username, user_question, answer)
        return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})

    # Call OpenRouter API
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        result = resp.json()
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        question_cache[cache_key] = answer
        save_cache()
        save_question_and_answer(username, user_question, answer)

        return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})
    except Exception as e:
        print(f"Error in /ask: {e}")
        return jsonify({'error': 'AI service error'}), 500

@app.route('/settings')
def settings():
    memory = {
        "traits": session.get('traits', []),
        "more_info": session.get('more_info', ''),
        "enable_memory": session.get('enable_memory', False)
    }
    return render_template('settings.html', memory=memory, theme=session.get('theme'), language=session.get('language'))

if __name__ == '__main__':
    with app.app_context():
        app.run(debug=True)
