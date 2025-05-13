import speech_recognition as sr

recognizer = sr.Recognizer()

# Replace 'audio_file.wav' with the path to your actual audio file
audio_file_path = 'audio_file.wav'

try:
    with sr.AudioFile(audio_file_path) as source:
        audio = recognizer.record(source)
    # Recognize speech using Google Web Speech API
    text = recognizer.recognize_google(audio)
    print("Recognized text:", text)
except FileNotFoundError:
    print("Audio file not found.")
except sr.UnknownValueError:
    print("Speech Recognition could not understand audio.")
except sr.RequestError as e:
    print(f"Could not request results; {e}")