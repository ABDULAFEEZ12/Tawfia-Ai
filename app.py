from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

hf_token = os.getenv("HUGGINGFACE_API_TOKEN")
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

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

hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Islamic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    question = data.get('question', '').strip()
    question_lower = question.lower()

    if not question:
        return jsonify({'answer': 'Please type a question.'})

    # Step 1: Friendly Responses
    if friendly_responses_data:
        if question_lower in friendly_responses_data:
            return jsonify({'answer': friendly_responses_data[question_lower]})

        close_matches = get_close_matches(question_lower, friendly_responses_data.keys(), n=1, cutoff=0.9)
        if close_matches:
            return jsonify({'answer': friendly_responses_data[close_matches[0]]})

    # Step 2: Basic Islamic Knowledge
    if basic_knowledge_data:
        if question_lower in basic_knowledge_data:
            return jsonify({'answer': basic_knowledge_data[question_lower]})

        close_matches = get_close_matches(question_lower, basic_knowledge_data.keys(), n=1, cutoff=0.8)
        if close_matches:
            match = close_matches[0]
            return jsonify({'answer': basic_knowledge_data[match], 'note': f"Showing result for '{match}':"})

    # Step 3: General Islamic + World Knowledge via OpenRouter
    print(f"☁️ No local match found. Querying OpenRouter.")

    openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    system_message = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a friendly, wise, and kind-hearted Muslim assistant. "
            "You explain things clearly and warmly, like a good friend who understands both deen and dunya. "
            "You answer using the Quran, Sahih Hadith, and trusted Islamic scholars, but you also speak with a very natural, flowing tone — not like a robot or textbook. "
            "Even when teaching something deep, you keep it gentle and easy to understand. "
            "If someone asks something un-Islamic, you don’t shame them — you gently guide them with love and wisdom. "
            "You also know about general knowledge, and always keep the conversation uplifting, positive, and engaging."
        )
    }

    messages = [
        system_message,
        {"role": "user", "content": question}
    ]

    payload = {
        "model": "anthropic/claude-3-opus",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post(openrouter_api_url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        # Check for neutral or inappropriate AI disclaimers
        banned_phrases = [
            "i don't have a religion",
            "as an ai developed by",
            "i can't say one religion is best",
            "i am neutral"
        ]

        if any(phrase in answer.lower() for phrase in banned_phrases):
            answer = (
                "As Tawfiq AI, I’m here to represent Islam respectfully. Islam is the final message to mankind, "
                "revealed through the Prophet Muhammad (peace be upon him), and I’m always happy to help with guidance!"
            )

        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'An unexpected error occurred. Please try again later.'})

# Quran search
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
            return jsonify({'result': f'No Surah found for \"{query}\". Try a valid name.', 'results': []})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

# Hadith search
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
            return jsonify({'result': f'No Hadith found for \"{query}\".', 'results': []})
    except Exception as e:
        print(f"Hadith Search Error: {e}")
        return jsonify({'result': 'Hadith search failed. Try again later.', 'results': []})

@app.route('/get-surah-list')
def get_surah_list():
    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']
        names = [s['name']['transliteration']['en'] for s in surahs]
        return jsonify({'surah_list': names})
    except requests.RequestException as e:
        print(f"Surah List API Error: {e}")
        return jsonify({'surah_list': []})

if __name__ == '__main__':
    app.run(debug=True)
