from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# --- Load datasets once ---
# Hadith data
hadith_data = {}
try:
    json_path = r'C:\DATA\sahih_bukhari_coded.json'  # or your absolute path
    print(f"Trying to load Hadith data from: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        hadith_data = json.load(f)
except FileNotFoundError:
    # fallback relative path
    json_path = os.path.join(os.path.dirname(__file__), 'DATA', 'sahih_bukhari_coded.json')
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            hadith_data = json.load(f)
    except FileNotFoundError:
        print("❌ Hadith data file not found.")

# Basic Islamic Knowledge
basic_knowledge_data = {}
try:
    knowledge_path = r'C:\DATA\basic_islamic_knowledge.json'
    print(f"Trying to load Basic Knowledge from: {knowledge_path}")
    with open(knowledge_path, 'r', encoding='utf-8') as f:
        basic_knowledge_data = json.load(f)
except FileNotFoundError:
    knowledge_path = os.path.join(os.path.dirname(__file__), 'DATA', 'basic_islamic_knowledge.json')
    try:
        with open(knowledge_path, 'r', encoding='utf-8') as f:
            basic_knowledge_data = json.load(f)
    except FileNotFoundError:
        print("❌ Basic Islamic Knowledge file not found.")

# Friendly Responses
friendly_responses_data = {}
try:
    friendly_path = r'C:\DATA\friendly_responses.json'
    print(f"Trying to load Friendly Responses from: {friendly_path}")
    with open(friendly_path, 'r', encoding='utf-8') as f:
        friendly_responses_data = json.load(f)
except FileNotFoundError:
    friendly_path = os.path.join(os.path.dirname(__file__), 'DATA', 'friendly_responses.json')
    try:
        with open(friendly_path, 'r', encoding='utf-8') as f:
            friendly_responses_data = json.load(f)
    except FileNotFoundError:
        print("❌ Friendly Responses file not found.")

# --- Utility: normalize questions for matching ---
import string

def normalize_question(q):
    if not isinstance(q, str):
        return ""
    return q.lower().translate(str.maketrans('', '', string.punctuation)).strip()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    question = data.get('question', '').strip()
    if not question:
        return jsonify({'answer': 'Please type a question.'})

    normalized_q = normalize_question(question)

    # Step 1: Friendly responses
    if friendly_responses_data:
        if normalized_q in friendly_responses_data:
            print(f"✨ Exact friendly match for: {question}")
            return jsonify({'answer': friendly_responses_data[normalized_q]})
        # Close match in friendly responses
        keys_norm = [normalize_question(k) for k in friendly_responses_data.keys()]
        match = get_close_matches(normalized_q, keys_norm, n=1, cutoff=0.9)
        if match:
            # Find original key
            original_key = next(k for k, nk in zip(friendly_responses_data.keys(), keys_norm) if nk == match[0])
            print(f"✨ Close friendly match ({match[0]}) for: {question}")
            return jsonify({'answer': friendly_responses_data[original_key]})

    # Step 2: Basic Islamic Knowledge
    if basic_knowledge_data:
        if normalized_q in basic_knowledge_data:
            print(f"📚 Exact knowledge match for: {question}")
            return jsonify({'answer': basic_knowledge_data[normalized_q]})
        # Close match in knowledge
        keys_norm = [normalize_question(k) for k in basic_knowledge_data.keys()]
        match = get_close_matches(normalized_q, keys_norm, n=1, cutoff=0.8)
        if match:
            original_key = next(k for k, nk in zip(basic_knowledge_data.keys(), keys_norm) if nk == match[0])
            print(f"📚 Close knowledge match ({match[0]}) for: {question}")
            return jsonify({'answer': basic_knowledge_data[original_key], 'note': f"Showing result for '{original_key}':"})

    # Step 3: Fallback to DeepAI API
    print(f"☁️ No local match for: {question}. Calling DeepAI.")
    deepai_api_key = os.getenv("DEEPAI_API_KEY")
    if not deepai_api_key:
        return jsonify({'answer': 'Tawfiq AI is not configured correctly (API key missing). Please contact the admin.'})

    try:
        # Compose prompt for DeepAI
        islamic_prompt = (
            "You are Tawfiq AI, an Islamic assistant. Your responses are strictly based on the Quran and authentic Hadith. "
            "Answer only questions about Islam. If the question is not about Islam, politely decline."
            f"\n\nUser Question: {question}\n\nAI Answer:"
        )

        response = requests.post(
            "https://api.deepai.org/api/text-generator",
            headers={'api-key': deepai_api_key},
            data={'text': islamic_prompt}
        )
        response.raise_for_status()
        result = response.json()
        answer = result.get('output', 'Could not generate response from DeepAI.')
        # Check if AI declines
        decline_phrases = ["i cannot answer", "not related to islam", "i can only answer islamic questions"]
        if any(phrase in answer.lower() for phrase in decline_phrases):
            answer = "I apologize, but I can only provide information related to Islam based on the Quran and authentic Hadith."

        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"DeepAI API error: {e}")
        return jsonify({'answer': 'Tawfiq AI is facing an issue. Please try later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'Unexpected error. Try later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip()
    normalized_q = normalize_question(query)

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []})

    try:
        res = requests.get('https://api.quran.gading.dev/surah')
        res.raise_for_status()
        surahs = res.json()['data']
        surah_map = {normalize_question(s['name']['transliteration']['en']): s['number'] for s in surahs}
        match = get_close_matches(normalized_q, surah_map.keys(), n=1, cutoff=0.7)
        if match:
            surah_number = surah_map[match[0]]
            verses_res = requests.get(f'https://api.quran.gading.dev/surah/{surah_number}')
            verses_res.raise_for_status()
            data_surah = verses_res.json()['data']
            title = f"{data_surah['name']['transliteration']['en']} ({data_surah['name']['short']})"
            verses_list = []
            for v in data_surah['verses']:
                verses_list.append({
                    'surah_name': data_surah['name']['transliteration']['en'],
                    'surah_number': surah_number,
                    'verse_number': v['number']['inSurah'],
                    'translation': v['translation']['en'],
                    'arabic_text': v['text']['arab']
                })
            return jsonify({'surah_title': title, 'results': verses_list})
        else:
            return jsonify({'result': f'No Surah found for \"{query}\". Try another name.', 'results': []})
    except requests.RequestException as e:
        print(f"Quran API error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip()
    normalized_q = normalize_question(query)

    if not query:
        return jsonify({'result': 'Please provide a Hadith keyword.', 'results': []})

    # Remove common prefixes
    normalized_q = normalized_q.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '')

    if not hadith_data:
        return jsonify({'result': 'Hadith data not loaded.', 'results': []})

    results_list = []
    count = 0
    max_results = 5

    try:
        for volume in hadith_data.get('volumes', []):
            for book in volume.get('books', []):
                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').lower()
                    keywords = hadith.get('keywords', [])
                    normalized_text = normalize_question(text)
                    normalized_keywords = [normalize_question(k) for k in keywords]
                    if (normalized_q in normalized_text) or any(normalized_q in k for k in normalized_keywords):
                        if count < max_results:
                            results_list.append({
                                'volume_number': volume.get('volume_number'),
                                'book_number': book.get('book_number'),
                                'book_name': book.get('book_name'),
                                'hadith_info': hadith.get('info', f'Volume {volume.get("volume_number")}, Book {book.get("book_number")}'),
                                'narrator': hadith.get('by', 'Unknown narrator'),
                                'text': hadith.get('text', '')
                            })
                            count += 1
                        else:
                            break
                if count >= max_results:
                    break
            if count >= max_results:
                break
        if results_list:
            return jsonify({'results': results_list})
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".', 'results': []})
    except Exception as e:
        print(f"Error during Hadith search: {e}")
        return jsonify({'result': 'Error searching Hadith. Try again later.', 'results': []})

@app.route('/get-surah-list')
def get_surah_list():
    try:
        res = requests.get('https://api.quran.gading.dev/surah')
        res.raise_for_status()
        surahs = res.json()['data']
        names = [s['name']['transliteration']['en'] for s in surahs]
        return jsonify({'surahs': names})
    except requests.RequestException as e:
        print(f"Error loading Surah list: {e}")
        return jsonify({'surahs': []})

if __name__ == '__main__':
    app.run(debug=True)
