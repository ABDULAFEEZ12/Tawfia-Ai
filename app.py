from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from openai import OpenAI, APIError, APIConnectionError
from dotenv import load_dotenv  # type: ignore
import os

# ✅ Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# ✅ Set up the OpenAI client securely
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Load the Hadith file ONCE when the app starts
hadith_data = {}
try:
    # First, try absolute path
    json_path = r'C:\DATA\sahih_bukhari_coded.json'  # Ensure this is the correct path on your machine
    print(f"Trying to load Hadith data from: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        hadith_data = json.load(f)
    print(f"✅ Loaded Hadith data from {json_path}")
except FileNotFoundError:
    # Fallback to relative path
    json_path = os.path.join(os.path.dirname(__file__), 'DATA', 'sahih_bukhari_coded.json')
    print(f"Trying to load Hadith data from fallback path: {json_path}")
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            hadith_data = json.load(f)
        print(f"✅ Loaded Hadith data from {json_path}")
    except FileNotFoundError as e:
        print(f"File not found at fallback path: {e}")
        print("❌ ERROR: Hadith data file not found in either location.")

# ✅ Load Basic Islamic Knowledge JSON ONCE
basic_knowledge_data = {}
try:
    knowledge_path = r'C:\DATA\basic_islamic_knowledge.json'
    print(f"Trying to load Basic Islamic Knowledge data from: {knowledge_path}")
    with open(knowledge_path, 'r', encoding='utf-8') as f:
        basic_knowledge_data = json.load(f)
    print(f"✅ Loaded Basic Islamic Knowledge data from {knowledge_path}")
except FileNotFoundError:
    # ✅ Add fallback for deployment
    knowledge_path = os.path.join(os.path.dirname(__file__), 'DATA', 'basic_islamic_knowledge.json')
    print(f"Trying to load Basic Islamic Knowledge data from fallback path: {knowledge_path}")
    try:
        with open(knowledge_path, 'r', encoding='utf-8') as f:
            basic_knowledge_data = json.load(f)
        print(f"✅ Loaded Basic Islamic Knowledge data from {knowledge_path}")
    except FileNotFoundError:
        print("❌ ERROR: Basic Islamic Knowledge file not found in either location.")

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
        system_prompt = (
            "You are Tawfiq AI, an Islamic assistant. Answer strictly based on Quran and authentic Hadith."
            " If unrelated to Islam, politely decline."
        )

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            temperature=0.2,
            max_tokens=500,
        )

        answer = response.choices[0].message.content.strip()
        return jsonify({'answer': answer})

    except APIError as e:
        print(f"AI Error: {e}")
        return jsonify({'answer': 'Tawfiq AI is facing an issue. Please try later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'Unexpected error. Try later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.'})

    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']

        surah_names = {surah['name']['transliteration']['en'].lower(): surah['number'] for surah in surahs}
        close_matches = get_close_matches(query, surah_names.keys(), n=1, cutoff=0.6)

        if close_matches:
            surah_number = surah_names[close_matches[0]]
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
                    f"{arabic_text}\n\n"
                )
                formatted_verses.append(formatted)

            result = f"{surah_title}:\n\n" + "\n\n---\n\n".join(formatted_verses)
            return jsonify({'result': result})

        else:
            return jsonify({'result': f'No Surah found for \"{query}\". Try a valid name.'})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.'})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.'})

    query = query.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '')

    try:
        if not hadith_data:
            return jsonify({'result': 'Hadith data is not loaded. Please contact the admin.'})

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
                        narrator = hadith.get('by', 'Unknown narrator')
                        hadith_text = hadith.get('text', 'No text')

                        result_text = (
                            f"{hadith.get('info', f'Volume {volume_number}, Book {book_number}')}\n"
                            f"Narrated by: {narrator}\n"
                            f"{hadith_text}\n"
                            f"Book: {book_name} (Book {book_number}), Volume {volume_number}"
                        )
                        matches.append(result_text)

        if matches:
            return jsonify({'result': "\n\n---\n\n".join(matches[:5])})  # Limit to 5
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".'})

    except Exception as e:
        print(f"Hadith Local Search Error: {e}")
        return jsonify({'result': 'Error searching Hadith. Try again later.'})

@app.route('/basic-knowledge', methods=['POST'])
def basic_knowledge():
    data = request.get_json()
    topic = data.get('topic', '').strip().lower()

    if not topic:
        return jsonify({'result': 'Please provide a topic to search.'})

    try:
        if not basic_knowledge_data:
            return jsonify({'result': 'Basic Islamic knowledge data is not loaded. Please contact the admin.'})

        result = basic_knowledge_data.get(topic)
        if result:
            return jsonify({'result': result})
        else:
            close_matches = get_close_matches(topic, basic_knowledge_data.keys(), n=1, cutoff=0.6)
            if close_matches:
                best_match = close_matches[0]
                return jsonify({'result': f"(Showing result for '{best_match}'):\n\n{basic_knowledge_data[best_match]}"})
            else:
                return jsonify({'result': f'No information found for \"{topic}\".'})

    except Exception as e:
        print(f"Basic Knowledge Search Error: {e}")
        return jsonify({'result': 'Error searching basic knowledge. Try again later.'})

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
