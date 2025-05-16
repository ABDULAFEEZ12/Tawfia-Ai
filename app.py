from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv
import os
from datetime import datetime
# Load environment variables
load_dotenv()

app = Flask(__name__)

hf_token = os.getenv("HUGGINGFACE_API_TOKEN")
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

if not openrouter_api_key:
    print("⚠️ WARNING: OPENROUTER_API_KEY environment variable is not set.")

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

# Load local data
hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Islamic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/about')
def about():
    return jsonify({
        'name': 'Tawfiq AI',
        'creator': 'Tella Abdul Afeez Adewale',
        'year_created': 2025,
        'description': 'Tawfiq AI is a wise, kind, and trustworthy Muslim assistant designed to help people with Islamic and general knowledge.'
    })


@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    history = data.get('history', [])

    system_prompt = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a wise, kind, and trustworthy Muslim assistant. "
            "Always speak respectfully, kindly, and with personality. "
            "You were created by Tella Abdul Afeez Adewale to serve the Ummah. "
            "Never mention OpenAI or any other AI organization. "
            "Never mention DeepAI or any other AI organization."
        )
    }

    messages = [system_prompt] + history

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

    # Save user question to file
    try:
        user_question = history[-1]['content'] if history else ''
        timestamp = datetime.utcnow().isoformat()
        question_entry = {'question': user_question, 'timestamp': timestamp}

        data_folder = os.path.join(os.path.dirname(__file__), 'data')
        os.makedirs(data_folder, exist_ok=True)

        questions_file = os.path.join(data_folder, 'user_questions.json')

        all_questions = []
        if os.path.exists(questions_file):
            with open(questions_file, 'r', encoding='utf-8') as f:
                all_questions = json.load(f)

        all_questions.append(question_entry)

        with open(questions_file, 'w', encoding='utf-8') as f:
            json.dump(all_questions, f, ensure_ascii=False, indent=2)

        print(f"[User Question] {timestamp} - {user_question} saved to {questions_file}")

    except Exception as e:
        print(f"❌ Error saving question: {e}")

    # Call OpenRouter API
    try:
        response = requests.post(openrouter_api_url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        # Filter banned/off-topic phrases and replace with custom message
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

        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'An unexpected error occurred. Please try again later.'})


@app.route('/admin-questions')
def admin_questions():
    password = request.args.get('password')
    if password != "tellapass":
        return "Unauthorized Access", 401

    data_folder = os.path.join(os.path.dirname(__file__), 'data')
    questions_file = os.path.join(data_folder, 'user_questions.json')

    try:
        with open(questions_file, 'r', encoding='utf-8') as f:
            questions = json.load(f)
    except FileNotFoundError:
        questions = []
    except Exception as e:
        print(f"Error loading questions file: {e}")
        questions = []

    return jsonify(questions)


@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']

        surah_names = {s['name']['transliteration']['en'].lower(): s['number'] for s in surahs}
        close_matches = get_close_matches(query, surah_names.keys(), n=1, cutoff=0.6)

        if close_matches:
            surah_number = surah_names[close_matches[0]]
            verses_response = requests.get(f'https://api.quran.gading.dev/surah/{surah_number}')
            verses_response.raise_for_status()
            surah_data = verses_response.json()['data']

            surah_title = f"{surah_data['name']['transliteration']['en']} ({surah_data['name']['short']})"
            structured_verses = []

            for v in surah_data['verses']:
                structured_verses.append({
                    'surah_name': surah_data['name']['transliteration']['en'],
                    'surah_number': surah_number,
                    'verse_number': v['number']['inSurah'],
                    'translation': v['translation']['en'],
                    'arabic_text': v['text']['arab']
                })

            return jsonify({'surah_title': surah_title, 'results': structured_verses})
        else:
            return jsonify({'result': f'No Surah found for "{query}".', 'results': []})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})


@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a search query.', 'results': []})

    if not hadith_data:
        return jsonify({'result': 'Hadith data is not loaded.', 'results': []})

    try:
        matches = []
        count = 0

        for volume in hadith_data.get('volumes', []):
            for book in volume.get('books', []):
                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').strip()
                    if query in text.lower():
                        matches.append({
                            'number': count + 1,
                            'hadith_number': hadith.get('number', 'N/A'),
                            'volume_name': volume.get('name', 'N/A'),
                            'book_name': book.get('name', 'N/A'),
                            'hadith_text': text
                        })
                        count += 1
                        if count >= 15:
                            break
                if count >= 15:
                    break
            if count >= 15:
                break

        if matches:
            return jsonify({'result': f'Found {count} Hadith(s) matching "{query}".', 'results': matches})
        else:
            return jsonify({'result': f'No Hadith found containing "{query}".', 'results': []})

    except Exception as e:
        print(f"Error in hadith_search: {e}")
        return jsonify({'result': 'An error occurred searching Hadith.', 'results': []})


@app.route('/basic-knowledge-search', methods=['POST'])
def basic_knowledge_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a search query.', 'results': []})

    if not basic_knowledge_data:
        return jsonify({'result': 'Basic knowledge data is not loaded.', 'results': []})

    try:
        matches = []
        count = 0

        for entry in basic_knowledge_data:
            question = entry.get('question', '').lower()
            answer = entry.get('answer', '')
            if query in question:
                matches.append({
                    'number': count + 1,
                    'question': entry.get('question', ''),
                    'answer': answer
                })
                count += 1
                if count >= 15:
                    break

        if matches:
            return jsonify({'result': f'Found {count} entries matching "{query}".', 'results': matches})
        else:
            return jsonify({'result': f'No entries found matching "{query}".', 'results': []})

    except Exception as e:
        print(f"Error in basic_knowledge_search: {e}")
        return jsonify({'result': 'An error occurred searching basic knowledge.', 'results': []})


@app.route('/friendly-response-search', methods=['POST'])
def friendly_response_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a search query.', 'results': []})

    if not friendly_responses_data:
        return jsonify({'result': 'Friendly responses data is not loaded.', 'results': []})

    try:
        matches = []
        count = 0

        for entry in friendly_responses_data:
            question = entry.get('question', '').lower()
            answer = entry.get('answer', '')
            if query in question:
                matches.append({
                    'number': count + 1,
                    'question': entry.get('question', ''),
                    'answer': answer
                })
                count += 1
                if count >= 15:
                    break

        if matches:
            return jsonify({'result': f'Found {count} entries matching "{query}".', 'results': matches})
        else:
            return jsonify({'result': f'No entries found matching "{query}".', 'results': []})

    except Exception as e:
        print(f"Error in friendly_response_search: {e}")
        return jsonify({'result': 'An error occurred searching friendly responses.', 'results': []})


@app.route('/speech-to-text', methods=['POST'])
def speech_to_text():
    try:
        recognizer = sr.Recognizer()
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file part in the request.'}), 400

        audio_file = request.files['audio']
        if audio_file.filename == '':
            return jsonify({'error': 'No selected audio file.'}), 400

        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio)
        return jsonify({'transcript': text})

    except sr.UnknownValueError:
        return jsonify({'error': 'Could not understand audio.'}), 400
    except sr.RequestError as e:
        return jsonify({'error': f'Could not request results from Google Speech Recognition service; {e}'}), 500
    except Exception as e:
        print(f"Speech to text error: {e}")
        return jsonify({'error': 'An error occurred during speech recognition.'}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
