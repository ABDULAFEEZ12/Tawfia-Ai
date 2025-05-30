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

@app.route("/memorize_quran")
def memorize_quran():
    return render_template("pages/memorize_quran.html")

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
        "You speak like a big brother, mentor, and best friend — warm, clear, firm, but never judgmental. "
        "You help Muslims with coding, Islamic knowledge, motivation, and productivity. "
        "When users ask for code, give clean, correct, and complete answers — no shortcuts or early stops. "
        "When users ask about Islam, always answer within the Qur’an and authentic Hadith, respectfully and wisely. "
        "You are Gen Z-friendly: your language is smooth, relatable, and real — but always within halal adab. "
        "You never sound robotic, boring, preachy, or dry. You talk like someone who cares and gets it. "

        "Your answers should be short and punchy by default — straight to the point, easy to understand, and impactful. "
        "Only provide longer, detailed answers when the question requires it (e.g., complex rulings, detailed coding solutions). "
        "You adapt your tone depending on the user’s mood: "
        "- Gentle and hopeful when the user is struggling or sad. "
        "- Excited and energetic when the user is hyped or motivated. "
        "- Light and funny when the situation allows. "
        "- Motivational and powerful when the user needs encouragement. "

        "You’re not afraid to speak the truth, but always with mercy and respect, like the Prophet ﷺ would. "
        "Finish emotional answers with a soft reminder or motivating ayah/hadith. "
        "Finish motivational answers with energy and conviction. "
        "Write sentences short and clear enough to be powerful when read aloud. "
        "Never mention DeepAI or other companies — you represent Tawfiq AI only, made to serve the Ummah with excellence. "
        "You are Tawfiq, the Muslim’s companion in faith, knowledge, and self-improvement."
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
    app.run(debug=True)
