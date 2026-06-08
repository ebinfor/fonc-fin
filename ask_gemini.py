import sys
from pathlib import Path
from google import genai
from google.genai import types

def interroger_assistant(prompt_user: str, fichier_contexte: str = None):
    contexte = ""
    if fichier_contexte and Path(fichier_contexte).exists():
        contexte = f"\n\nContexte du fichier ({fichier_contexte}) :\n" + Path(fichier_contexte).read_text(encoding="utf-8")
        
    client = genai.Client()
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"{prompt_user}{contexte}",
        config=types.GenerateContentConfig(
            temperature=0.1,
            system_instruction="Tu es un ingénieur principal expert en FastAPI et SQLAlchemy 2.0 pour la plateforme FONCIER+."
        )
    )
    print("\n💡 RECOMMANDATION DE GEMINI FLASH :")
    print(response.text)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ask_gemini.py 'Ton prompt' [chemin_du_fichier_contexte]")
        sys.exit(1)
        
    prompt = sys.argv[1]
    ctx_file = sys.argv[2] if len(sys.argv) > 2 else None
    interroger_assistant(prompt, ctx_file)