from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv # type: ignore
import os

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Get DeepAI API key from environment variables
deepai_api_key = os.getenv("DEEPAI_API_KEY") # Ensure this is correctly named in your .env

# --- Function to load JSON data with proper path handling ---
def load_json_data(file_name, data_variable_name):
    """Loads JSON data from the DATA directory relative to the app.py file."""
    data = {}
    # Construct the path using os.path.join for platform independence
    file_path = os.path.join(os.path.dirname(__file__), 'DATA', file_name)
    print(f"Attempting to load {data_variable_name} data from: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ Successfully loaded {data_variable_name} data from {file_path}")
    except FileNotFoundError:
        print(f"❌ ERROR: {data_variable_name} data file not found at {file_path}")
        # You might want to raise an exception here if the data is critical
        # raise FileNotFoundError(f"{data_variable_name} data file not found")
    except json.JSONDecodeError as e:
        print(f"❌ JSON Decode Error in {file_path}: {e}")
        # You might want to raise an exception here as well
        # raise json.JSONDecodeError(f"JSON Decode Error in {file_path}: {e}", e.doc, e.pos)
    except Exception as e:
        print(f"❌ An unexpected error occurred while loading {file_name}: {e}")


    return data

# Load the data using the new function
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
    question_lower = question.lower() # Use lower case for matching

    if not question:
        return jsonify({'answer': 'Please type a question.'})

    # --- Step 1: Check Friendly Responses ---
    if friendly_responses_data:
        # Check for exact matches first
        if question_lower in friendly_responses_data:
            print(f"✨ Found exact match in friendly_responses for: {question}")
            return jsonify({'answer': friendly_responses_data[question_lower]})

        # Check for close matches in friendly responses
        close_friendly_matches = get_close_matches(question_lower, friendly_responses_data.keys(), n=1, cutoff=0.9) # Higher cutoff for friendly
        if close_friendly_matches:
             print(f"✨ Found close match in friendly_responses for: {question} -> {close_friendly_matches[0]}")
             return jsonify({'answer': friendly_responses_data[close_friendly_matches[0]]})


    # --- Step 2: Check Basic Islamic Knowledge ---
    if basic_knowledge_data:
        # Check for exact matches first
        if question_lower in basic_knowledge_data:
             print(f"📚 Found exact match in basic_knowledge for: {question}")
             # Return structured data for basic knowledge (optional, but good practice)
             return jsonify({'answer': basic_knowledge_data[question_lower]})

        # Check for close matches in basic knowledge
        close_knowledge_matches = get_close_matches(question_lower, basic_knowledge_data.keys(), n=1, cutoff=0.8) # Slightly lower cutoff for knowledge
        if close_knowledge_matches:
            best_match = close_knowledge_matches[0]
            print(f"📚 Found close match in basic_knowledge for: {question} -> {best_match}")
            # Return structured data for basic knowledge (optional, but good practice)
            return jsonify({'answer': basic_knowledge_data[best_match], 'note': f"Showing result for '{best_match}':"})


    # --- Step 3: Fallback to DeepAI API with Islamic Prompt ---
    print(f"☁️ No local match found for: {question}. Consulting DeepAI.")
    if not deepai_api_key:
         print("❌ ERROR: DeepAI API key not found in environment variables.")
         return jsonify({'answer': 'Tawfiq AI is not configured correctly (API key missing). Please contact the admin.'})

    try:
        # Using DeepAI's text generation API (adjust model name and parameters as needed)
        # Refer to DeepAI's text generation API documentation for details: https://deepai.org/machine-learning-models
        # Example using the 'text-generation' endpoint
        deepai_url = "https://api.deepai.org/api/text-generation"

        # Craft the prompt to guide the AI
        # This is the MOST IMPORTANT part for controlling the output
        islamic_prompt = (
            "You are Tawfiq AI, an Islamic assistant. Your purpose is to provide information strictly based on the Quran and authentic Hadith."
            " Answer only questions related to Islam. If a question is not about Islam, politely decline to answer it and state that you can only answer Islamic questions."
            " Do not provide opinions or information from other sources. Focus on factual Islamic knowledge."
            f"\n\nUser Question: {question}"
            "\n\nAI Answer:"
        )

        deepai_payload = {
            'text': islamic_prompt,
            # 'output_len': 500 # Example parameter - check documentation
            # You might want to experiment with 'temperature' here. Lower values (closer to 0)
            # can make the output more focused and less creative, which might be good
            # for staying within a specific domain.
        }

        response = requests.post(
            deepai_url,
            headers={'api-key': deepai_api_key},
            data=deepai_payload
        )

        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        deepai_result = response.json()
        answer = deepai_result.get('output', 'Could not generate response from DeepAI.')

        # Basic check to see if the AI declined (imperfect)
        # You can refine this based on typical decline phrases from the AI
        declined_phrases = ["i cannot answer", "not related to islam", "i can only answer islamic questions"]
        answer_lower = answer.lower()
        if any(phrase in answer_lower for phrase in declined_phrases):
             return jsonify({'answer': "I apologize, but I can only provide information related to Islam based on the Quran and authentic Hadith."})


        # Optional: Add a disclaimer if the answer comes from the external AI
        # answer = f"(AI generated) {answer}"

        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"DeepAI API Error: {e}")
        # Check if it's a 500 error specifically, though a general error is okay too
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 500:
             return jsonify({'answer': 'Tawfiq AI is facing a temporary issue with the external AI (server error). Please try later.'})
        else:
             return jsonify({'answer': 'Tawfiq AI is facing an issue with the external AI. Please try later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'Unexpected error. Try later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []}) # Return empty list for frontend

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
            # ✅ Prepare structured list of verses
            structured_verses = []

            for v in surah_data['verses']:
                structured_verses.append({
                    'surah_name': surah_data['name']['transliteration']['en'],
                    'surah_number': surah_number,
                    'verse_number': v['number']['inSurah'],
                    'translation': v['translation']['en'],
                    'arabic_text': v['text']['arab']
                })

            # ✅ Return structured data
            return jsonify({'surah_title': surah_title, 'results': structured_verses})

        else:
            return jsonify({'result': f'No Surah found for \"{query}\". Try a valid name.', 'results': []})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip().lower()

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.', 'results': []}) # Return empty list

    query = query.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '')

    try:
        if not hadith_data:
            return jsonify({'result': 'Hadith data is not loaded. Please contact the admin.', 'results': []})

        # ✅ Prepare structured list of Hadith matches
        structured_matches = []
        count = 0 # Keep track of results to limit

        for volume in hadith_data.get('volumes', []):
            volume_number = volume.get('volume_number', 'N/A')
            for book in volume.get('books', []):
                book_number = book.get('book_number', 'N/A')
                book_name = book.get('book_name', 'Unknown Book')

                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '').lower()
                    keywords = hadith.get('keywords', [])

                    if query in text or any(query in k.lower() for k in keywords):
                        if count < 5: # Limit to 5 results
                            structured_matches.append({
                                'volume_number': volume_number,
                                'book_number': book_number,
                                'book_name': book_name,
                                'hadith_info': hadith.get('info', f'Volume {volume_number}, Book {book_number}'),
                                'narrator': hadith.get('by', 'Unknown narrator'),
                                'text': hadith.get('text', 'No text')
                            })
                            count += 1
                        else:
                            break # Stop searching if we have 5 results
                if count >= 5:
                    break # Stop searching books if we have 5 results
            if count >= 5:
                break # Stop searching volumes if we have 5 results


        if structured_matches:
            # ✅ Return structured data
            return jsonify({'results': structured_matches})
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".', 'results': []})

    except Exception as e:
        print(f"Hadith Local Search Error: {e}")
        return jsonify({'result': 'Error searching Hadith. Try again later.', 'results': []})

# The original basic_knowledge route can remain, although the /ask route now also checks this data.
# It might be useful if you want a dedicated 'Basic Knowledge' search feature later.
# For now, the /ask route uses this data internally.
# @app.route('/basic-knowledge', methods=['POST'])
# def basic_knowledge_route():
#     data = request.get_json()
#     topic = data.get('topic', '').strip().lower()

#     if not topic:
#         return jsonify({'result': 'Please provide a topic to search.'})

#     try:
#         if not basic_knowledge_data:
#             return jsonify({'result': 'Basic Islamic knowledge data is not loaded. Please contact the admin.'})

#         result = basic_knowledge_data.get(topic)
#         if result:
#              return jsonify({'topic': topic, 'info': result})
#         else:
#             close_matches = get_close_matches(topic, basic_knowledge_data.keys(), n=1, cutoff=0.6)
#             if close_matches:
#                 best_match = close_matches[0]
#                 return jsonify({'topic': best_match, 'info': basic_knowledge_data[best_match], 'note': f"Showing result for '{best_match}':"})
#             else:
#                 return jsonify({'result': f'No information found for \"{topic}\".'})

#     except Exception as e:
#         print(f"Basic Knowledge Search Error: {e}")
#         return jsonify({'result': 'Error searching basic knowledge. Try again later.'})


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
