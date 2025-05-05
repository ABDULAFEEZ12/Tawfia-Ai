from flask import Flask, request, jsonify, render_template
import json
import requests
from difflib import get_close_matches
import openai
from openai import APIError
from dotenv import load_dotenv  # type: ignore
import os

# ✅ Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# ✅ Set up the OpenAI client securely
openai.api_key = os.getenv("OPENAI_API_KEY")

# ✅ Load the Hadith file ONCE when the app starts
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
hadith_path = os.path.join(BASE_DIR, 'DATA', 'sahih_bukhari_coded.json')

try:
    with open(hadith_path, 'r', encoding='utf-8') as f:
        hadith_data = json.load(f)
except FileNotFoundError:
    print(f"[ERROR] Hadith file not found at: {hadith_path}")
    hadith_data = {}  # fallback so app doesn’t crash

# ✅ Load basic Islamic knowledge from the JSON file
def load_basic_knowledge():
    knowledge_path = os.path.join(BASE_DIR, 'DATA', 'basic_islamic_knowledge.json')
    try:
        with open(knowledge_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] Basic knowledge file not found at: {knowledge_path}")
        return {}

# ✅ Function to get answer from local knowledge or fallback to OpenAI
def get_answer(user_question):
    basic_knowledge = load_basic_knowledge()

    # Check if the question is in the local knowledge
    if user_question in basic_knowledge:
        return basic_knowledge[user_question]

    # If not found, fallback to OpenAI
    return query_openai(user_question)

# ✅ Function to query OpenAI
def query_openai(user_question):
    try:
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=user_question,
            max_tokens=150
        )
        return response.choices[0].text.strip()
    except Exception as e:
        print(f"Error querying OpenAI: {e}")
        return "Error querying OpenAI. Please try again later."

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    question = data.get('question', '').strip()

    if not question:
        return jsonify({'answer': 'Please type a question.'})

    try:
        # Get the answer (either from local knowledge or OpenAI)
        answer = get_answer(question)

        return jsonify({'answer': answer})

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'Unexpected error. Try later.'})

# ✅ Quran Sector (using Gading API)
@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.'})

    try:
        # ✅ Get Surah list from Gading API
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']

        # ✅ Map surah names to IDs
        surah_names = {surah['name']['transliteration']['en'].lower(): surah['number'] for surah in surahs}
        close_matches = get_close_matches(query, surah_names.keys(), n=1, cutoff=0.6)

        if close_matches:
            surah_number = surah_names[close_matches[0]]
            # ✅ Fetch verses WITH translation
            verses_response = requests.get(
                f'https://api.quran.gading.dev/surah/{surah_number}'
            )
            verses_response.raise_for_status()
            surah_data = verses_response.json()['data']

            surah_title = f"{surah_data['name']['transliteration']['en']} ({surah_data['name']['short']})"
            formatted_verses = []

            for v in surah_data['verses']:
                ayah_num = v['number']['inSurah']
                translation = v['translation']['en']
                arabic_text = v['text']['arab']

                formatted = (
                    f"{surah_number}:{ayah_num} {translation}\n\n"
                    f"{arabic_text}"
                )
                formatted_verses.append(formatted)

            result = f"{surah_title}:\n\n" + "\n\n---\n\n".join(formatted_verses)
            return jsonify({'result': result})

        else:
            return jsonify({'result': f'No Surah found for \"{query}\". Try a valid name.'})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.'})

# ✅ Hadith Sector: Local JSON search
@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = (
        data.get('query', '')
        .strip()
        .lower()
        .replace('hadith on ', '')
        .replace('hadith by ', '')
        .replace('hadith talking about ', '')
    )

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.'})

    if not hadith_data:
        return jsonify({'result': 'Hadith data not loaded properly. Please try again later.'})

    try:
        matches = []
        for volume in hadith_data.get('volumes', []):
            volume_number = volume.get('volume_number', 'N/A')
            for book in volume.get('books', []):
                book_number = book.get('book_number', 'N/A')
                book_name = book.get('book_name', 'Unknown Book')

                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').lower()
                    keywords = hadith.get('keywords', [])

                    if query in text or any(query in k.lower() for k in keywords):
                        info = hadith.get('info', 'No info')
                        narrator = hadith.get('by', 'Unknown narrator')
                        hadith_text = hadith.get('text', 'No text')

                        result_text = (
                            f"Volume {volume_number}, Book {book_number}, Number {hadith.get('hadith_number', 'N/A')}\n"
                            f"Narrated by: {narrator}\n"
                            f"{hadith_text}\n"
                            f"Book: {book_name} (Book {book_number}), Volume {volume_number}"
                        )
                        matches.append(result_text)

        if matches:
            return jsonify({'result': "\n\n---\n\n".join(matches[:5])})  # ✅ Limit to first 5 matches
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".'})

    except Exception as e:
        print(f"Hadith Local Search Error: {e}")
        return jsonify({'result': 'Error searching Hadith. Try again later.'})

# ✅ Return Surah list for dropdown
@app.route('/get-surah-list')
def get_surah_list():
    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']
        surah_names = [s['name']['transliteration']['en'] for s in surahs]
        return jsonify({'surahs': surah_names})
    except requests.RequestException as e:
        print(f"Error loading Surah list: {e}")
        return jsonify({'surahs': []})

if __name__ == '__main__':
    app.run(debug=True)
