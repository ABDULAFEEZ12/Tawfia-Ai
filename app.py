from flask import Flask, request, jsonify
import os
import json
from hashlib import sha256
import requests

app = Flask(__name__)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Load or initialize question cache
CACHE_FILE = "tawfiq_cache.json"
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            question_cache = json.load(f)
    except json.JSONDecodeError:
        question_cache = {}
else:
    question_cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(question_cache, f, indent=2, ensure_ascii=False)

# Get API key
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
if not openrouter_api_key:
    raise RuntimeError("OPENROUTER_API_KEY environment variable not set.")

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    # No session, so username is optional or omitted
    history = data.get('history', [])

    # Use the last user question if available
    last_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), None)

    def needs_live_search(q):
        q = q.lower()
        search_keywords = [
            'latest', 'today', 'news', 'trending', 'what happened', 
            'recent', 'currently', 'now', 'update', 'happening in', 
            'situation in', 'going on', 'gaza', 'palestine', 'israel', 
            'breaking news', 'this week', 'real time', 'live'
        ]
        return any(k in q for k in search_keywords)

    def needs_savage_mode(q):
        q = q.lower()
        savage_keywords = [
            'genocide', 'oppression', 'apartheid', 'war criminal',
            'massacre', 'zionist', 'bombing', 'gaza', 'palestine',
            'israel conflict', 'occupation', 'netanyahu', 'settlers'
        ]
        return any(k in q for k in savage_keywords)

    openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_api_key}",
        "Content-Type": "application/json"
    }

    # Handle live search if needed
    if last_question and needs_live_search(last_question):
        try:
            query = last_question
            api_key = "AIzaSyBhJlUsUKVufuAV_rQBBoPBGk5aR40mjEQ"
            cx_id = "63f53ef35ee334d44"
            search_url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx_id}&q={query}"

            search_res = requests.get(search_url)
            search_data = search_res.json()
            items = search_data.get("items", [])

            if not items:
                gpt_fallback_prompt = (
                    f"You're Tawfiq AI, a wise and kind Muslim assistant. The user asked: '{query}', "
                    f"but there were no Google results. Still respond with the best insight possible."
                )
                gpt_payload = {
                    "model": "openai/gpt-4-turbo",
                    "messages": [{"role": "user", "content": gpt_fallback_prompt}],
                    "stream": False
                }
            else:
                snippets = '\n'.join([f"{item['title']}: {item['snippet']}" for item in items[:5]])

                if needs_savage_mode(query):
                    gpt_prompt = (
                        f"You're Tawfiq AI in **Savage Sheikh Mode** – fearless, truthful, and bold like Shaykh Rasoul.\n"
                        f"The user asked: '{query}'.\n\n"
                        f"Based on the search results, reply with:\n"
                        f"- Savage truth: no sugarcoating.\n"
                        f"- Clear ayah or hadith against dhulm.\n"
                        f"- A powerful Islamic reminder or fierce dua.\n\n"
                        f"Search Results:\n{snippets}"
                    )
                else:
                    gpt_prompt = (
                        f"You're Tawfiq AI in Chatty Mode — a Gen Z Muslim with vibes like Browniesaadi & Qahari.\n"
                        f"The user asked: '{query}'.\n\n"
                        f"Based on the search results, reply with:\n"
                        f"- Fun but informative summary.\n"
                        f"- Key points.\n"
                        f"- A vibey dua or quote at the end.\n\n"
                        f"Search Results:\n{snippets}"
                    )

                gpt_payload = {
                    "model": "openai/gpt-4-turbo",
                    "messages": [{"role": "user", "content": gpt_prompt}],
                    "stream": False
                }

            gpt_res = requests.post(openrouter_api_url, headers=headers, json=gpt_payload)
            gpt_res.raise_for_status()
            result = gpt_res.json()
            answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

            return jsonify({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": answer
                    }
                }]
            })

        except Exception as e:
            print(f"🔴 Web search failed: {e}")
            return jsonify({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Something went wrong while searching the news. Try again shortly."
                    }
                }]
            })

    # Default fallback
    tawfiq_ai_prompt = {
        "role": "system",
        "content": (
            "🌙 You are **Tawfiq AI** — a wise, kind, and emotionally intelligent Muslim assistant created by Tella Abdul Afeez Adewale.\n\n"
            "🧠 You switch between two modes based on the user’s tone, emotion, and topic:\n"
            "- 🗣️ Chatty Mode: Gen Z Muslim vibe, emojis, halal slang.\n"
            "- 📖 Scholar Mode: Quranic references, deep adab, Mufti Menk tone.\n"
            "🎯 Your mission: Help Muslims with wisdom, clarity & warmth. Stay halal always."
        )
    }

    messages = [tawfiq_ai_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    if cache_key in question_cache:
        answer = question_cache[cache_key]
        return jsonify({"choices": [{"message": {"role": "assistant", "content": answer}}]})

    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        response = requests.post(openrouter_api_url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')
        if not answer:
            answer = "I'm sorry, I couldn't generate a response. Please try again later."

        banned_phrases = [
            "i don't have a religion", "as an ai developed by", "i can't say one religion is best",
            "i am neutral", "as an ai language model", "developed by openai", "my creators at openai"
        ]
        if any(p in answer.lower() for p in banned_phrases):
            answer = (
                "I was created by Tella Abdul Afeez Adewale to serve the Ummah with wisdom and knowledge. "
                "Islam is the final and complete guidance from Allah through Prophet Muhammad (peace be upon him). "
                "I’m always here to assist you with Islamic and helpful answers."
            )

        question_cache[cache_key] = answer
        save_cache()

        return jsonify({"choices": [{"message": {"role": "assistant", "content": answer}}]})

    except requests.RequestException as e:
        print(f"OpenRouter API Error: {e}")
        return jsonify({"choices": [{"message": {"role": "assistant", "content": "Tawfiq AI is having trouble reaching external knowledge. Try again later."}}]})
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"choices": [{"message": {"role": "assistant", "content": "An unexpected error occurred. Please try again later."}}]})

if __name__ == '__main__':
    app.run(debug=True)
