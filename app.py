from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv  # type: ignore
import os
from transliterate import translit  # Keep if needed elsewhere

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# Your API key
deepai_api_key = os.getenv("DEEPAI_API_KEY")  # Ensure this is set

# --- Function to load JSON data ---
def load_json_data(file_name, data_variable_name):
    file_path = os.path.join(os.path.dirname(__file__), 'DATA', file_name)
    print(f"Attempting to load {data_variable_name} from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ Successfully loaded {data_variable_name}")
    except FileNotFoundError:
        print(f"❌ {data_variable_name} file not found at {file_path}")
        data = {}
    except json.JSONDecodeError as e:
        print(f"❌ JSON error in {file_path}: {e}")
        data = {}
    return data

# Load datasets
hadith_data = load_json_data('sahih_bukhari_coded.json', 'Hadith')
basic_knowledge_data = load_json_data('basic_islamic_knowledge.json', 'Basic Islamic Knowledge')
friendly_responses_data = load_json_data('friendly_responses.json', 'Friendly Responses')

# --- Readable transliteration function ---
def arabic_to_readable_transliteration(arabic_text):
    translit_map = {
        'ا': 'a',
        'ب': 'b',
        'ت': 't',
        'ث': 'th',
        'ج': 'j',
        'ح': 'ḥ',
        'خ': 'kh',
        'د': 'd',
        'ذ': 'dh',
        'ر': 'r',
        'ز': 'z',
        'س': 's',
        'ش': 'sh',
        'ص': 'ṣ',
        'ض': 'ḍ',
        'ط': 'ṭ',
        'ظ': 'ẓ',
        'ع': '‘',
        'غ': 'gh',
        'ف': 'f',
        'ق': 'q',
        'ك': 'k',
        'ل': 'l',
        'م': 'm',
        'ن': 'n',
        'ه': 'h',
        'و': 'w',
        'ي': 'y',
        'ء': "'",
        'ئ': '’',
        'ؤ': '’',
        'ة': 'a',
        # Add common multi-character combinations for better readability
        'لا': 'la',
        'مر': 'mar',
        'حب': 'hubb',
        'سلام': 'salaam',
        # Expand as needed for more accuracy
    }

    result = ''
    i = 0
    while i < len(arabic_text):
        # Check for digraphs first
        if i + 1 < len(arabic_text):
            pair = arabic_text[i:i+2]
            if pair in translit_map:
                result += translit_map[pair]
                i += 2
                continue
        # Single character fallback
        ch = arabic_text[i]
        result += translit_map.get(ch, ch)
        i += 1

    # Make the output more readable:
    # Capitalize first letter of each word
    words = result.split()
    words_cap = [w.capitalize() for w in words]
    return ' '.join(words_cap)

# --- Route definitions ---

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

    # 1. Friendly responses
    if friendly_responses_data:
        if question_lower in friendly_responses_data:
            return jsonify({'answer': friendly_responses_data[question_lower]})
        match = get_close_matches(question_lower, friendly_responses_data.keys(), n=1, cutoff=0.9)
        if match:
            return jsonify({'answer': friendly_responses_data[match[0]]})

    # 2. Basic Islamic Knowledge
    if basic_knowledge_data:
        if question_lower in basic_knowledge_data:
            return jsonify({'answer': basic_knowledge_data[question_lower]})
        match = get_close_matches(question_lower, basic_knowledge_data.keys(), n=1, cutoff=0.8)
        if match:
            return jsonify({'answer': basic_knowledge_data[match[0]], 'note': f"Showing result for '{match[0]}':"})

    # 3. Use DeepAI API
    if not deepai_api_key:
        return jsonify({'answer': 'Tawfiq AI is not configured properly (API key missing).'})
    try:
        deepai_url = "https://api.deepai.org/api/text-generation"
        prompt = (
            "You are Tawfiq AI, an Islamic assistant. Provide only accurate Islamic info from Quran & Hadith."
            " Answer questions about Islam only. Decline politely if not about Islam."
            f"\n\nUser Question: {question}"
            "\n\nAI Answer:"
        )
        response = requests.post(
            deepai_url,
            headers={'api-key': deepai_api_key},
            data={'text': prompt}
        )
        response.raise_for_status()
        answer = response.json().get('output', 'Could not generate response.')
        decline_phrases = ["i cannot answer", "not related to islam", "i can only answer islamic questions"]
        if any(phrase in answer.lower() for phrase in decline_phrases):
            answer = "I apologize, I can only answer questions based on the Quran and authentic Hadith."
        return jsonify({'answer': answer})
    except Exception:
        return jsonify({'answer': 'Error communicating with AI.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()
    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})
    try:
        res = requests.get('https://api.quran.gading.dev/surah')
        res.raise_for_status()
        surahs = res.json()['data']
        name_map = {s['name']['transliteration']['en'].lower(): s['number'] for s in surahs}
        match = get_close_matches(query, name_map.keys(), n=1, cutoff=0.6)
        if match:
            surah_number = name_map[match[0]]
            verses_res = requests.get(f'https://api.quran.gading.dev/surah/{surah_number}')
            verses_res.raise_for_status()
            surah_data = verses_res.json()['data']
            title = f"{surah_data['name']['transliteration']['en']} ({surah_data['name']['short']})"
            results = []

            for v in surah_data['verses']:
                arabic_text = v['text']['arab']
                transliteration = arabic_to_readable_transliteration(arabic_text)
                results.append({
                    'surah_name': surah_data['name']['transliteration']['en'],
                    'surah_number': surah_number,
                    'verse_number': v['number']['inSurah'],
                    'translation': v['translation']['en'],
                    'arabic_text': arabic_text,
                    'transliteration': transliteration
                })
            return jsonify({'surah_title': title, 'results': results})
        else:
            return jsonify({'result': f'No matching Surah for "{query}".', 'results': []})
    except Exception:
        return jsonify({'result': 'Error fetching Quran data.', 'results': []})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()
    if not query:
        return jsonify({'result': 'Please provide a Hadith keyword.', 'results': []})
    query = query.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '')
    results_list = []
    count = 0
    try:
        if not hadith_data:
            return jsonify({'result': 'Hadith data not loaded.', 'results': []})
        for volume in hadith_data.get('volumes', []):
            for book in volume.get('books', []):
                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').lower()
                    keywords = hadith.get('keywords', [])
                    if query in text or any(query in k.lower() for k in keywords):
                        if count < 5:
                            results_list.append({
                                'volume_number': volume.get('volume_number', 'N/A'),
                                'book_number': book.get('book_number', 'N/A'),
                                'book_name': book.get('book_name', 'Unknown Book'),
                                'hadith_info': hadith.get('info', f"Volume {volume.get('volume_number')}, Book {book.get('book_number')}"),
                                'narrator': hadith.get('by', 'Unknown narrator'),
                                'text': hadith.get('text', 'No text')
                            })
                            count += 1
                        else:
                            break
                if count >= 5:
                    break
            if count >= 5:
                break
        return jsonify({'results': results_list})
    except Exception:
        return jsonify({'result': 'Error searching Hadith.'})

@app.route('/get-surah-list')
def get_surah_list():
    try:
        res = requests.get('https://api.quran.gading.dev/surah')
        res.raise_for_status()
        surahs = res.json()['data']
        names = [s['name']['transliteration']['en'] for s in surahs]
        return jsonify({'surahs': names})
    except:
        return jsonify({'surahs': []})

if __name__ == '__main__':
    app.run(debug=True)
