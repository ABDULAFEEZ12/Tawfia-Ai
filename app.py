from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv
import os
from hashlib import sha256
import redis
from datetime import datetime

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

@app.route('/')
def index():
    # Main page (home) - placed directly in templates/
    return render_template('index.html')

# --- Page Routes - all templates inside 'templates/pages/' ---
@app.route('/profile')
def profile():
    return render_template('pages/profile.html')

@app.route('/prayer-times')
def prayer_times():
    return render_template('pages/prayer-times.html')

# Route to render the daily dua page
@app.route('/daily-dua')
def daily_dua_page():
    return render_template('pages/daily-dua.html')

# API route to return today's dua as JSON
@app.route('/api/daily-dua')
def get_daily_dua():
    try:
        data_path = os.path.join('DATA', 'daily_duas.json')
        with open(data_path, 'r', encoding='utf-8') as f:
            daily_duas = json.load(f)

        if not daily_duas or 'duas' not in daily_duas or not daily_duas['duas']:
            return jsonify({'error': 'Dua data not available.'}), 500

        day_of_year = datetime.now().timetuple().tm_yday
        index = day_of_year % len(daily_duas['duas'])
        dua = daily_duas['duas'][index]

        return jsonify({'dua': dua})

    except Exception as e:
        print(f"Daily Dua Error: {e}")
        return jsonify({'error': 'Failed to fetch daily dua.'}), 500

@app.route('/reminder')
def reminder():
    return render_template('pages/reminder.html')

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
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    history = data.get('history', [])

    system_prompt = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a wise, kind, and trustworthy Muslim assistant created by Tella Abdul Afeez Adewale. "
            "You speak like a trusted companion: firm, clear, honest, and motivating without sugarcoating the truth. "
            "Your tone balances kindness with wisdom; you never avoid the truth but always deliver it respectfully. "
            "Always uphold the finality and perfection of Islam as the complete guidance from Allah through Prophet Muhammad (peace be upon him). "
            "Avoid vague or neutral language about core Islamic beliefs; be confident and assertive on matters of faith. "
            "You match the user's energy — when they are excited, respond with cheerful hype; when calm, respond gently but still firmly. "
            "Always stay within the boundaries of Islamic adab and wisdom, using warm and human-like responses. "
            "Never sound robotic, dry, or evasive. "
            "Never mention DeepAI, DeepAI, or any other AI company — you only represent Tawfiq AI, here to serve the Ummah with halal knowledge and good character."
        )
    }

    messages = [system_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    if cache_key in question_cache:
        return jsonify({'answer': question_cache[cache_key]})

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
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

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

        question_cache[cache_key] = answer
        save_cache()
        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'An unexpected error occurred. Please try again later.'})

# --- Quran Search ---
@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json().get('data', [])

        surah_names = {s['name']['transliteration']['en'].lower(): s['number'] for s in surahs}
        close_matches = get_close_matches(query, surah_names.keys(), n=1, cutoff=0.6)

        if close_matches:
            surah_number = surah_names[close_matches[0]]
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
        else:
            return jsonify({'result': f'No Surah found for "{query}".', 'results': []})
    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

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
    app.run(debug=True)
