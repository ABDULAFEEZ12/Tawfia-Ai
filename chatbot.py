import json
import random

# Comprehensive basic Islamic knowledge data
basic_knowledge_data = {
    "who_is_allah": {
        "question": "Who is Allah?",
        "answer": "Allah is the one God in Islam, the Creator of the universe, who is unique and without partners.",
        "keywords": ["who is allah", "who Allah", "what is Allah", "Allah"]
    },
    "who_is_final_prophet": {
        "question": "Who is the final Prophet of Islam?",
        "answer": "The final Prophet of Islam is Prophet Muhammad (Peace Be Upon Him).",
        "keywords": ["final prophet", "who is the final prophet", "who is Muhammad", "Muhammad"]
    },
    "name_of_religion": {
        "question": "What is the name of the religion revealed to Prophet Muhammad?",
        "answer": "The name of the religion revealed to Prophet Muhammad (Peace Be Upon Him) is Islam.",
        "keywords": ["name of the religion", "what is the name of the religion", "Islam"]
    },
    "meaning_la_ilaha_illallah": {
        "question": "What is the meaning of 'La ilaha illallah'?",
        "answer": "The meaning of 'La ilaha illallah' is 'There is no god but Allah'.",
        "keywords": ["la ilaha illallah", "meaning of la ilaha illallah"]
    },
    "meaning_of_islam": {
        "question": "What does 'Islam' mean?",
        "answer": "Islam means 'submission' or 'surrender' to the will of Allah.",
        "keywords": ["meaning of Islam", "Islam means"]
    },
    "who_are_the_angels": {
        "question": "Who are the angels in Islam?",
        "answer": "Angels in Islam are beings created by Allah from light, who perform various tasks including delivering messages to prophets.",
        "keywords": ["who are the angels", "angels in Islam"]
    },
    "what_is_zakat": {
        "question": "What is Zakat?",
        "answer": "Zakat is an obligatory form of charity in Islam, usually calculated as 2.5% of savings.",
        "keywords": ["what is zakat", "zakat"]
    },
    "who_is_prophet_muhammad": {
        "question": "Who is the last prophet?",
        "answer": "The final Prophet of Islam is Prophet Muhammad (Peace Be Upon Him).",
        "keywords": ["who is Muhammad", "last prophet"]
    },
    # Add more entries as needed...
}

def load_hadiths(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading hadiths: {e}")
        exit()

def load_friendly_responses(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading friendly responses: {e}")
        exit()

def get_friendly_reply(user_input, responses):
    user_input = user_input.strip().lower()
    
    if any(greeting in user_input for greeting in ["hi", "hello", "assalamu alaikum", "as-salam alaykum", "hey", "what's up"]):
        return random.choice(responses["greetings"])
    
    if any(farewell in user_input for farewell in ["bye", "goodbye", "see you", "take care"]):
        return random.choice(responses["farewells"])
    
    if any(thanks in user_input for thanks in ["thank you", "thanks", "jazakallah", "thank you very much"]):
        return random.choice(responses["gratitudes"])
    
    if "how are you" in user_input:
        return "I'm doing great, thank you! Just here to assist you!"

    return None

def get_hadith(user_input, hadiths):
    if "tell me a hadith" in user_input or "any hadith" in user_input:
        all_hadiths = []
        for volume in hadiths['volumes']:
            for book in volume['books']:
                all_hadiths.extend(book['hadiths'])
        if all_hadiths:
            chosen = random.choice(all_hadiths)
            return f"{chosen['info']}\n\n{chosen['by']}\n\n{chosen['text']}"
    
    numbers = [word for word in user_input.split() if word.isdigit()]
    if numbers:
        hadith_num = int(numbers[0])
        for volume in hadiths['volumes']:
            for book in volume['books']:
                for hadith in book['hadiths']:
                    if hadith.get('hadith_number') == hadith_num:
                        return f"{hadith['info']}\n\n{hadith['by']}\n\n{hadith['text']}"
        return f"Hadith {hadith_num} not found. Please try another number."

    keywords = user_input.replace("hadith about", "").replace("hadith on", "").replace("any hadith about", "").replace("tell me a hadith on", "").strip().lower()
    if not keywords:
        return None

    matching_hadiths = []
    for volume in hadiths['volumes']:
        for book in volume['books']:
            for hadith in book['hadiths']:
                if 'text' in hadith and any(keyword in hadith['text'].lower() for keyword in keywords.split()):
                    matching_hadiths.append(hadith)

    if matching_hadiths:
        chosen = random.choice(matching_hadiths)
        return f"{chosen['info']}\n\n{chosen['by']}\n\n{chosen['text']}"

    return "Sorry, I couldn't find a Hadith on that topic. Try different keywords."

def get_basic_knowledge_reply(user_input, knowledge):
    user_input_lower = user_input.strip().lower()
    for entry, details in knowledge.items():
        if any(keyword in user_input_lower for keyword in details["keywords"]):
            return details["answer"]
    return None

def log_query(user_input):
    with open('user_queries.txt', 'a', encoding='utf-8') as f:
        f.write(user_input + '\n')

def main():
    hadith_filename = 'sahih_bukhari_coded.json'
    responses_filename = 'friendly_responses.json'
    
    hadiths = load_hadiths(hadith_filename)
    responses = load_friendly_responses(responses_filename)

    print("✨ Welcome to Tawfiq - Your Smart Islamic Companion! ✨")
    print("🔍 Created with love by TELLA ABDUL AFEEZ ADEWALE")
    name = input("🤔 May I know your name? ")
    print(f"Awesome to meet you, {name}!")
    print("You can ask for Hadiths, basic Islamic questions, or just chat!")
    print("👉 Type 'exit' whenever you're ready to end our conversation.")

    while True:
        user_input = input(f"{name}: ").strip()
        if user_input.lower() in ['exit', 'quit', 'leave']:
            confirm = input("Are you sure you want to exit? (yes/no): ").lower()
            if confirm == 'yes':
                print(f"TAWFIQ: Thank you for spending time with me, {name}! Have a wonderful day filled with blessings!")
                break
        
        log_query(user_input)

        # Check for friendly replies
        friendly_reply = get_friendly_reply(user_input, responses)
        if friendly_reply:
            print(f"TAWFIQ: {friendly_reply}")
            continue  # If we have a friendly reply, continue to the next input

        # First check for basic knowledge replies
        basic_knowledge_reply = get_basic_knowledge_reply(user_input, basic_knowledge_data)
        if basic_knowledge_reply:
            print(f"TAWFIQ: {basic_knowledge_reply}")
            continue  # If we have a basic knowledge reply, continue to the next input

        # Finally, check for Hadiths
        response = get_hadith(user_input, hadiths)
        print(f"TAWFIQ: {response if response else 'Sorry, I couldn’t understand. Can you rephrase or ask in a different way?'}")

# Run the main function
if __name__ == "__main__":
    main()