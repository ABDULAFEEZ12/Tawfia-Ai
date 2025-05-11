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

# Load JSON datasets
def load_json_data(file_name, data_variable_name):
    data = {}
    file_path = os.path.join(os.path.dirname(__file__), 'DATA', file_name)
    print(f"Attempting to load {data_variable_name} from: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ Successfully loaded {data_variable_name}")
    except FileNotFoundError:
        print(f"❌ {data_variable_name} file not found.")
    except json.JSONDecodeError as e:
        print(f"❌ JSON Decode Error in {file_name}: {e}")
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
    question = data.get('question', '').strip().lower()

    if not question:
        return jsonify({'answer': 'Please type a question.'})

    # Friendly Responses
    if question in friendly_responses_data:
        return jsonify({'answer': friendly_responses_data[question]})

    match = get_close_matches(question, friendly_responses_data.keys(), n=1, cutoff=0.9)
    if match:
        return jsonify({'answer': friendly_responses_data[match[0]]})

    # Basic Islamic Knowledge
    if question in basic_knowledge_data:
        return jsonify({'answer': basic_knowledge_data[question]})

    match = get_close_matches(question, basic_knowledge_data.keys(), n=1, cutoff=0.8)
    if match:
        return jsonify({'answer': basic_knowledge_data[match[0]], 'note': f"Showing result for '{match[0]}':"})

    # AI fallback via OpenRouter
    print("☁️ No match found. Querying OpenRouter...")
    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    system_message = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a friendly, wise, and kind-hearted Muslim assistant. "
            "You answer using the Quran, Sahih Hadith, and trusted scholars. "
            "Speak clearly and compassionately. Be firm on truth, gentle in tone."
        )
    }

    messages = [system_message, {"role": "user", "content": question}]
    payload = {
        "model": "anthropic/claude-3-opus",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        banned_phrases = ["i don't have a religion", "as an ai", "i can't say one religion is best", "i am neutral"]
        if any(phrase in answer.lower() for phrase in banned_phrases):
            answer = (
                "As Tawfiq AI, I’m here to represent Islam respectfully. "
                "Islam is the final message to mankind, and I’m always happy to help with guidance!"
            )

        return jsonify({'answer': answer})
    except Exception as e:
        print(f"❌ AI Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is having trouble reaching external knowledge. Try again later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    try:
        chapters_response = requests.get('https://api.quran.com/v4/chapters')
        chapters_response.raise_for_status()
        chapters = chapters_response.json()['chapters']

        chapter_names = {c['name_simple'].lower(): c['id'] for c in chapters}
        match = get_close_matches(query, chapter_names.keys(), n=1, cutoff=0.6)

        if match:
            chapter_id = chapter_names[match[0]]
            verses_response = requests.get(f'https://api.quran.com/v4/quran/verses/by_chapter/{chapter_id}?language=en&words=false')
            verses_response.raise_for_status()
            verses_data = verses_response.json()

            results = [{
                'chapter_id': v['chapter_id'],
                'verse_number': v['verse_number'],
                'verse_key': v['verse_key'],
                'text_uthmani': v['text_uthmani']
            } for v in verses_data['verses']]

            return jsonify({'chapter_name': match[0].title(), 'results': results})
        else:
            return jsonify({'result': f'No Surah found for \"{query}\".', 'results': []})
    except Exception as e:
        print(f"❌ Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data.', 'results': []})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()
    query = query.replace('hadith on ', '').replace('hadith about ', '')

    if not query:
        return jsonify({'result': 'Please provide a keyword.', 'results': []})

    if not hadith_data:
        return jsonify({'result': 'Hadith data not loaded.', 'results': []})

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
                                'volume': volume.get('volume_number'),
                                'book': book.get('book_name'),
                                'narrator': hadith.get('by', 'Unknown'),
                                'text': hadith.get('text')
                            })
                            count += 1
                        else:
                            break

        if matches:
            return jsonify({'results': matches})
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".', 'results': []})
    except Exception as e:
        print(f"❌ Hadith Search Error: {e}")
        return jsonify({'result': 'Error during Hadith search.', 'results': []})

@app.route('/get-surah-list')
def get_surah_list():
    try:
        response = requests.get('https://api.quran.com/v4/chapters')
        response.raise_for_status()
        chapters = response.json()['chapters']
        return jsonify({'surah_list': [c['name_simple'] for c in chapters]})
    except Exception as e:
        print(f"❌ Surah List API Error: {e}")
        return jsonify({'surah_list': []})

@app.route('/get-recitations')
def get_recitations():
    try:
        response = requests.get('https://api.quran.com/v4/recitations')
        response.raise_for_status()
        return jsonify({'recitations': response.json()['recitations']})
    except Exception as e:
        print(f"❌ Recitations API Error: {e}")
        return jsonify({'recitations': []})

@app.route('/get-translations')
def get_translations():
    try:
        response = requests.get('https://api.quran.com/v4/quran/translations?language=en')
        response.raise_for_status()
        return jsonify({'translations': response.json()['translations']})
    except Exception as e:
        print(f"❌ Translations API Error: {e}")
        return jsonify({'translations': []})

if __name__ == '__main__':
    app.run(debug=True)
