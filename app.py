from flask import Flask, request, jsonify, render_template, render_template_string
import requests
import json
import os
from difflib import get_close_matches
from dotenv import load_dotenv
from datetime import datetime

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

    # --- Save the user question ---
    try:
        user_question = history[-1]['content'] if history else ''
        timestamp = datetime.utcnow().isoformat()
        question_entry = {'question': user_question, 'timestamp': timestamp}

        questions_file = 'user_questions.json'
        all_questions = []

        if os.path.exists(questions_file):
            with open(questions_file, 'r', encoding='utf-8') as f:
                all_questions = json.load(f)

        all_questions.append(question_entry)

        with open(questions_file, 'w', encoding='utf-8') as f:
            json.dump(all_questions, f, ensure_ascii=False, indent=2)

        # --- Log question to Render logs ---
        print(f"[User Question] {timestamp} - {user_question}")

    except Exception as e:
        print(f"❌ Error saving question: {e}")

    # --- Call the model API ---
    system_prompt = {
        "role": "system",
        "content": (
            "You are Tawfiq AI — a wise, kind, and trustworthy Muslim assistant. "
            "Always speak respectfully, kindly, and with personality. "
            "You were created by Tella Abdul Afeez Adewale to serve the Ummah. "
            "Never mention OpenAI or any other AI organization."
        )
    }

    messages = [system_prompt] + history

    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

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

# --- Admin Questions Viewer ---
@app.route('/admin-questions')
def admin_questions():
    password = request.args.get('password')
    if password != "tellapass":
        return "Unauthorized Access", 401

    try:
        with open('user_questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
    except FileNotFoundError:
        questions = []

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Questions</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 30px; background: #f7f7f7; }
            h1 { color: #333; }
            .question-box {
                background: white;
                border: 1px solid #ccc;
                padding: 15px;
                margin-bottom: 10px;
                border-radius: 6px;
            }
            .timestamp {
                font-size: 12px;
                color: #777;
            }
        </style>
    </head>
    <body>
        <h1>User Questions</h1>
        {% for q in questions %}
            <div class="question-box">
                <div>{{ q['question'] }}</div>
                <div class="timestamp">{{ q['timestamp'] }}</div>
            </div>
        {% endfor %}
    </body>
    </html>
    """
    return render_template_string(html_template, questions=questions)

# --- Stub Endpoints (Optional Implementation Later) ---
@app.route('/quran-search', methods=['POST'])
def quran_search():
    return jsonify({'message': 'Quran search not implemented yet.'})

@app.route('/hadith-search', methods=['POST'])
def hadith_search():
    return jsonify({'message': 'Hadith search not implemented yet.'})

@app.route('/basic-knowledge', methods=['POST'])
def basic_knowledge():
    return jsonify({'message': 'Basic Islamic knowledge search not implemented yet.'})

@app.route('/friendly-response', methods=['POST'])
def friendly_response():
    return jsonify({'message': 'Friendly response not implemented yet.'})

@app.route('/get-surah-list')
def get_surah_list():
    return jsonify({'message': 'Surah list not implemented yet.'})

# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True)
