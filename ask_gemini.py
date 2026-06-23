import os
import sys
import time
from pathlib import Path
from google import genai
from google.api_core import exceptions as google_exceptions
from google.genai import types

DEFAULT_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2

def interroger_assistant(prompt_user: str, fichier_contexte: str = None):
    contexte = ""
    if fichier_contexte and Path(fichier_contexte).exists():
        contexte = f"\n\nContexte du fichier ({fichier_contexte}) :\n" + Path(fichier_contexte).read_text(encoding="utf-8")

    client = genai.Client()
    contents = f"{prompt_user}{contexte}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    system_instruction="Tu es un ingénieur principal expert en FastAPI et SQLAlchemy 2.0 pour la plateforme FONCIER+."
                )
            )
            print("\n💡 RECOMMANDATION DE GEMINI FLASH :")
            print(response.text)
            return
        except Exception as exc:
            if isinstance(exc, google_exceptions.ResourceExhausted):
                retry_delay = getattr(exc, 'retry_delay', None)
                if retry_delay is None:
                    retry_delay = INITIAL_BACKOFF_SECONDS * attempt

                print(f"\n⚠️  Quota dépassé (tentative {attempt}/{MAX_RETRIES}).")
                print(f"   {str(exc)}")

                if attempt == MAX_RETRIES:
                    print("   Arrêt des tentatives après plusieurs échecs de quota.")
                    raise

                print(f"   Nouvelle tentative dans {retry_delay} secondes...")
                time.sleep(retry_delay)
                continue

            if isinstance(exc, google_exceptions.GoogleAPICallError):
                print("\n⚠️  Erreur d'API Gemini :")
                print(str(exc))
                raise

            print("\n⚠️  Erreur inattendue lors de l'appel Gemini :")
            print(str(exc))
            raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ask_gemini.py 'Ton prompt' [chemin_du_fichier_contexte]")
        sys.exit(1)

    prompt = sys.argv[1]
    ctx_file = sys.argv[2] if len(sys.argv) > 2 else None
    interroger_assistant(prompt, ctx_file)
