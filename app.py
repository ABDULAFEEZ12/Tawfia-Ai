from flask import Flask, request, jsonify, render_template
import requests
import json
from difflib import get_close_matches
from dotenv import load_dotenv # type: ignore
import os
import string # Import the string module for punctuation removal

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

# --- Function to normalize user input for matching ---
def normalize_question(question):
    """Converts question to lowercase and removes punctuation for matching."""
    if not isinstance(question, str):
        return "" # Handle non-string input gracefully
    normalized = question.lower()
    # Remove punctuation
    normalized = normalized.translate(str.maketrans('', '', string.punctuation))
    return normalized.strip()

# Load the data using the new function
# IMPORTANT: Ensure your JSON keys in these files are the normalized form
# (lowercase, no punctuation) for the best exact matching results.
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

    if not question:
        return jsonify({'answer': 'Please type a question.'})

    # Normalize the user's question once
    normalized_question = normalize_question(question)
    print(f"Normalized user question: {normalized_question}")

    # --- Step 1: Check Friendly Responses ---
    if friendly_responses_data:
        # Check for exact matches first using normalized question
        if normalized_question in friendly_responses_data:
            print(f"✨ Found exact match in friendly_responses for: {question}")
            return jsonify({'answer': friendly_responses_data[normalized_question]})

        # Check for close matches in friendly responses using normalized question and normalized keys
        # Create a list of normalized keys from the friendly responses data
        normalized_friendly_keys = [normalize_question(key) for key in friendly_responses_data.keys()]
        close_friendly_matches_normalized = get_close_matches(normalized_question, normalized_friendly_keys, n=1, cutoff=0.9) # Higher cutoff for friendly

        if close_friendly_matches_normalized:
             # Find the original key from the normalized match to get the correct response
             # This assumes normalized keys are unique - if not, this needs more logic
             original_key = next(key for key in friendly_responses_data.keys() if normalize_question(key) == close_friendly_matches_normalized[0])
             print(f"✨ Found close match in friendly_responses for: {question} -> {original_key}")
             return jsonify({'answer': friendly_responses_data[original_key]})


    # --- Step 2: Check Basic Islamic Knowledge ---
    if basic_knowledge_data:
        # Check for exact matches first using normalized question
        if normalized_question in basic_knowledge_data:
             print(f"📚 Found exact match in basic_knowledge for: {question}")
             # Return structured data for basic knowledge (optional, but good practice)
             return jsonify({'answer': basic_knowledge_data[normalized_question]})

        # Check for close matches in basic knowledge using normalized question and normalized keys
        # Create a list of normalized keys from the basic knowledge data
        normalized_knowledge_keys = [normalize_question(key) for key in basic_knowledge_data.keys()]
        close_knowledge_matches_normalized = get_close_matches(normalized_question, normalized_knowledge_keys, n=1, cutoff=0.8) # Slightly lower cutoff for knowledge

        if close_knowledge_matches_normalized:
            # Find the original key from the normalized match
            original_key = next(key for key in basic_knowledge_data.keys() if normalize_question(key) == close_knowledge_matches_normalized[0])
            print(f"📚 Found close match in basic_knowledge for: {question} -> {original_key}")
            # Return structured data for basic knowledge (optional, but good practice)
            return jsonify({'answer': basic_knowledge_data[original_key], 'note': f"Showing result for '{original_key}':"})


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
            f"\n\nUser Question: {question}" # Use the original question here for the AI
            "\n\nAI Answer:"
        )

        deepai_payload = {
            'text': islamic_prompt,
            # 'output_len': 500 # Example parameter - check documentation
            # 'temperature': 0.5 # Example: Lower temperature for less creativity
        }

        response = requests.post(
            deepai_url,
            headers={'api-key': deepai_api_key},
            data=deepai_payload
        )

        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        deepai_result = response.json()
        # DeepAI's text generation response is often in 'output_text' or 'output'
        # Check DeepAI documentation for the specific model you are using
        answer = deepai_result.get('output', 'Could not generate response from DeepAI.')

        # Basic check to see if the AI declined (imperfect)
        # You can refine this based on typical decline phrases from the AI
        declined_phrases = ["i cannot answer", "not related to islam", "i can only answer islamic questions", "i am not able to help with that"]
        answer_lower = answer.lower()
        if any(phrase in answer_lower for phrase in declined_phrases):
             return jsonify({'answer': "I apologize, but I can only provide information related to Islam based on the Quran and authentic Hadith. Please ask an Islamic question."})


        # Optional: Add a disclaimer if the answer comes from the external AI
        # answer = f"(AI generated) {answer}"

        return jsonify({'answer': answer})

    except requests.RequestException as e:
        print(f"DeepAI API Error: {e}")
        # Check if it's a 500 error specifically, though a general error is okay too
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code >= 500:
             return jsonify({'answer': 'Tawfiq AI is facing a temporary issue with the external AI (server error). Please try later.'})
        elif isinstance(e, requests.exceptions.HTTPError) and e.response.status_code >= 400:
             return jsonify({'answer': 'Tawfiq AI received a bad response from the external AI. Please check your request or try later.'})
        else:
             return jsonify({'answer': f'Tawfiq AI is facing an issue communicating with the external AI: {e}. Please try later.'})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'answer': 'An unexpected error occurred. Try later.'})

@app.route('/quran-search', methods=['POST'])
def quran_search():
    data = request.get_json()
    query = data.get('query', '').strip() # Keep original case for API call if needed, normalize for matching
    normalized_query = normalize_question(query)


    if not query:
        return jsonify({'result': 'Please provide a Surah name.', 'results': []}) # Return empty list for frontend

    try:
        response = requests.get('https://api.quran.gading.dev/surah')
        response.raise_for_status()
        surahs = response.json()['data']

        # Create a dictionary mapping normalized Surah names to their numbers
        surah_names_normalized = {normalize_question(surah['name']['transliteration']['en']): surah['number'] for surah in surahs}

        # Use normalized query to find close matches in normalized names
        close_matches_normalized = get_close_matches(normalized_query, surah_names_normalized.keys(), n=1, cutoff=0.7) # Adjusted cutoff slightly


        if close_matches_normalized:
            best_match_normalized = close_matches_normalized[0]
            surah_number = surah_names_normalized[best_match_normalized]

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
            return jsonify({'result': f'No Surah found matching \"{query}\". Try a valid name.', 'results': []})

    except requests.RequestException as e:
        print(f"Quran API Error: {e}")
        return jsonify({'result': 'Error fetching Quran data. Try again.', 'results': []})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    data = request.get_json()
    query = data.get('query', '').strip()
    # Normalize the query for searching within Hadith text/keywords
    normalized_query = normalize_question(query)

    if not query:
        return jsonify({'result': 'Please provide a Hadith search keyword.', 'results': []}) # Return empty list

    # Remove common prefixes before normalizing
    search_terms = normalized_query.replace('hadith on ', '').replace('hadith by ', '').replace('hadith talking about ', '').split() # Split into words after removing prefixes

    try:
        if not hadith_data:
            return jsonify({'result': 'Hadith data is not loaded. Please contact the admin.', 'results': []})

        # ✅ Prepare structured list of Hadith matches
        structured_matches = []
        count = 0 # Keep track of results to limit
        max_results = 5 # Define the maximum number of results

        for volume in hadith_data.get('volumes', []):
            volume_number = volume.get('volume_number', 'N/A')
            for book in volume.get('books', []):
                book_number = book.get('book_number', 'N/A')
                book_name = book.get('book_name', 'Unknown Book')

                for hadith in book.get('hadiths', []):
                    text = hadith.get('text', '')
                    keywords = hadith.get('keywords', [])

                    # Normalize Hadith text and keywords for matching
                    normalized_text = normalize_question(text)
                    normalized_keywords = [normalize_question(k) for k in keywords]

                    # Check if any search term is present in the normalized text or normalized keywords
                    # Using 'any' allows searching for multiple keywords if split
                    if any(term in normalized_text for term in search_terms) or \
                       any(term in normalized_keyword for normalized_keyword in normalized_keywords for term in search_terms):

                        if count < max_results: # Limit to max_results
                            structured_matches.append({
                                'volume_number': volume_number,
                                'book_number': book_number,
                                'book_name': book_name,
                                'hadith_info': hadith.get('info', f'Volume {volume_number}, Book {book_number}'),
                                'narrator': hadith.get('by', 'Unknown narrator'),
                                'text': hadith.get('text', 'No text') # Use original text for display
                            })
                            count += 1
                        else:
                            break # Stop searching if we have enough results
                if count >= max_results:
                    break # Stop searching books if we have enough results
            if count >= max_results:
                break # Stop searching volumes if we have enough results


        if structured_matches:
            # ✅ Return structured data
            return jsonify({'results': structured_matches})
        else:
            return jsonify({'result': f'No Hadith found for \"{query}\".', 'results': []})

    except Exception as e:
        print(f"Hadith Local Search Error: {e}")
        return jsonify({'result': 'Error searching Hadith. Try again later.', 'results': []})


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
    # Ensure the DATA directory exists
    if not os.path.exists('DATA'):
        os.makedirs('DATA')

    # Create dummy JSON files if they don't exist for testing
    # In a real app, you'd manage these files' creation/content separately
    basic_knowledge_path = os.path.join('DATA', 'basic_islamic_knowledge.json')
    friendly_responses_path = os.path.join('DATA', 'friendly_responses.json')
    hadith_data_path = os.path.join('DATA', 'sahih_bukhari_coded.json')

    if not os.path.exists(basic_knowledge_path):
        dummy_knowledge = {
            # Keys are normalized (lowercase, no punctuation)
            "who is allah": "In Islam, Allah is the singular and unique God, the Creator and Sustainer of the universe. Muslims believe there is no deity worthy of worship except Allah.",
            "what is the quran": "The Quran is the holy book of Islam, believed by Muslims to be the literal word of God revealed to the Prophet Muhammad.",
            "what is islam": "Islam is a monotheistic religion founded by the Prophet Muhammad in the 7th century. It is the second-largest religion in the world."
        }
        with open(basic_knowledge_path, 'w', encoding='utf-8') as f:
            json.dump(dummy_knowledge, f, indent=4)
        print(f"Created dummy knowledge base file at {basic_knowledge_path}")

    if not os.path.exists(friendly_responses_path):
         dummy_friendly = {
             # Keys are normalized (lowercase, no punctuation)
             "salam": "Wa alaikum assalam!",
             "hello": "Hello!",
             "hi": "Hi there!",
             "how are you": "Alhamdulillah (Praise be to God), I am functioning well. How can I assist you today?",
             "thank you": "You're welcome!",
             "jazakallah khair": "Wa iyyakum (And upon you)!"
         }
         with open(friendly_responses_path, 'w', encoding='utf-8') as f:
             json.dump(dummy_friendly, f, indent=4)
         print(f"Created dummy friendly responses file at {friendly_responses_path}")

    # Note: Creating a dummy sahih_bukhari_coded.json is complex due to its structure.
    # Ensure you have a valid one in your DATA directory.
    if not os.path.exists(hadith_data_path):
        print(f"❗️ Hadith data file not found at {hadith_data_path}. Hadith search will not work.")
        # You might want to create a minimal dummy structure if needed for testing
        # dummy_hadith = {"volumes": [{"volume_number": 1, "books": []}]}
        # with open(hadith_data_path, 'w', encoding='utf-8') as f:
        #      json.dump(dummy_hadith, f, indent=4)


    app.run(debug=True)
