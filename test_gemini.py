from google import genai
import os

client = genai.Client(api_key=os.getenv("API_KEY"))

response = client.models.generate_content(
    model="gemini-1.5-flash",
    contents="Generate 1 MCQ question on IoT with options A B C D"
)

print(response.text)