from flask import Flask, request, jsonify, render_template, Response
import requests
import json
import time
from difflib import get_close_matches
from dotenv import load_dotenv
import os

# Load environment variables
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

    # System prompt
    system_prompt = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a wise, kind, and trustworthy Muslim assistant. "
            "Always speak respectfully, kindly, and with personality. "
            "Build responses based on previous conversation context."
        )
    }
    messages = [system_prompt] + history

    # Call OpenRouter API with streaming support
    def generate():
        # API URL and headers
        openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter_api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "openai/gpt-4-turbo",
            "messages": messages,
            "stream": True  # Enable streaming
        }

        try:
            # Make the POST request with stream=True
            with requests.post(openrouter_api_url, headers=headers, json=payload, stream=True) as resp:
                resp.raise_for_status()
                # Stream chunks from the response
                for line in resp.iter_lines():
                    if line:
                        # Assuming the response is JSON lines, parse it
                        try:
                            data = json.loads(line.decode('utf-8'))
                            # Extract the delta content
                            delta = data.get('choices', [{}])[0].get('delta', {}).get('content', '')
                            if delta:
                                yield delta
                        except Exception as e:
                            print("Error parsing streamed line:", e)
        except requests.RequestException as e:
            print(f"Streaming API Error: {e}")
            yield "Sorry, I am having trouble connecting right now."
        # End of generator

    return Response(generate(), content_type='text/plain')

# --- Other routes (search functions) remain unchanged ---

@app.route('/quran-search', methods=['POST'])
def quran_search():
    # ... your existing code ...
    pass

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    # ... your existing code ...
    pass

@app.route('/get-surah-list')
def get_surah_list():
    # ... your existing code ...
    pass

if __name__ == '__main__':
    app.run(debug=True)
