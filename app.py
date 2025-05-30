from flask import Flask, request, jsonify, render_template
import requests
import json
import os
from hashlib import sha256
from dotenv import load_dotenv
import redis
from datetime import datetime
from difflib import get_close_matches

# --- Load environment variables ---
load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
if not openrouter_api_key:
    raise RuntimeError("Please set the OPENROUTER_API_KEY environment variable.")

# --- Redis Cache ---
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))
redis_db = int(os.getenv("REDIS_DB", 0))
redis_password = os.getenv("REDIS_PASSWORD", None)
r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)

# --- File-based Cache ---
CACHE_FILE = "tawfiq_cache.json"
question_cache = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            question_cache = json.load(f)
    except json.JSONDecodeError:
        question_cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(question_cache, f, indent=2, ensure_ascii=False)

# --- Load JSON Data ---
def load_json_data(file_name, label):
    try:
        file_path = os.path.join(os.path.dirname(__file__), 'data', file_name)
        with open(file_path, 'r', encoding='utf-8') as f:
            print(f"✅ Loaded {label}")
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load {label}: {e}")
        return {}

hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')
daily_duas_data = load_json_data('daily_duas.json', 'Daily Duas')
motivation_data = load_json_data('islamic_motivation.json', 'Motivation')

# --- Flask App ---
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/profile')
def profile():
    return render_template('pages/profile.html')

@app.route('/memorize-quran')
def memorize_quran():
    try:
        surah_dir = os.path.join('DATA', 'surah')
        surah_files = sorted(os.listdir(surah_dir), key=lambda x: int(os.path.splitext(x)[0]))
        surahs = []
        for filename in surah_files:
            filepath = os.path.join(surah_dir, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                surahs.append(json.load(f))
        return render_template('memorize_quran.html', surahs=surahs)
    except Exception as e:
        print(f"Surah Load Error: {e}")
        return "Error loading surahs", 500

@app.route('/prayer-times')
def prayer_times():
    return render_template('pages/prayer-times.html')

@app.route('/daily-dua')
def daily_dua():
    duas = daily_duas_data.get('duas', [])
    return render_template('pages/daily-dua.html', duas=duas)

@app.route('/reminder')
def reminder():
    return render_template('pages/reminder.html')

@app.route('/motivation')
def motivation():
    quotes = motivation_data.get('quotes', [])
    return render_template('pages/islamic_motivation.html', motivations=quotes)

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

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    history = data.get('history', [])

    system_prompt = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a wise, kind, and trustworthy Muslim assistant created by Tella Abdul Afeez Adewale..."
        )
    }

    messages = [system_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    if cache_key in question_cache:
        return jsonify({'answer': question_cache[cache_key]})

    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        answer = result['choices'][0]['message']['content']

        banned_phrases = [
            "i don't have a religion", "as an ai developed by", "i can't say one religion is best",
            "i am neutral", "as an ai language model", "developed by openai", "my creators at openai"
        ]
        if any(phrase in answer.lower() for phrase in banned_phrases):
            answer = (
                "I was created by Tella Abdul Afeez Adewale to serve the Ummah with wisdom and knowledge. "
                "Islam is the final and complete guidance from Allah through Prophet Muhammad (peace be upon him)."
            )

        question_cache[cache_key] = answer
        save_cache()
        return jsonify({'answer': answer})
    except Exception as e:
        print(f"Ask Route Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    try:
        surah_api = 'https://api.quran.gading.dev/surah'
        surahs = requests.get(surah_api).json().get('data', [])
        surah_map = {s['name']['transliteration']['en'].lower(): s['number'] for s in surahs}
        match = get_close_matches(query, surah_map.keys(), n=1, cutoff=0.6)

        if match:
            surah_number = surah_map[match[0]]
            verses_data = requests.get(f"{surah_api}/{surah_number}").json().get('data', {})
            results = [
                {
                    'surah_name': verses_data['name']['transliteration']['en'],
                    'verse_number': v['number']['inSurah'],
                    'translation': v['translation']['en'],
                    'arabic_text': v['text']['arab']
                } for v in verses_data['verses']
            ]
            return jsonify({'result': f"Found Surah: {match[0]}", 'results': results})
        else:
            return jsonify({'result': "No match found.", 'results': []})
    except Exception as e:
        print(f"Quran Search Error: {e}")
        return jsonify({'result': 'Error searching Quran.', 'results': []})

# --- Run App ---
if __name__ == '__main__':
    app.run(debug=True)
