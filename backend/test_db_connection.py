import os
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

def test_connection():
    # Récupération de l'URL depuis les variables d'environnement
    db_url = os.getenv("DATABASE_URL")
    
    if not db_url:
        print("Erreur : La variable d'environnement DATABASE_URL n'est pas définie.")
        return

    print(f"Tentative de connexion à l'adresse : {db_url.split('@')[-1]}")

    try:
        # Création du moteur avec un timeout de 10 secondes
        engine = create_engine(db_url, connect_args={"connect_timeout": 10})
        
        # Tentative d'exécution d'une requête simple
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("Succès : Connexion à la base de données établie avec succès !")
            
    except SQLAlchemyError as e:
        print("Échec de la connexion.")
        print(f"Détail de l'erreur : {str(e)}")
    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")

if __name__ == "__main__":
    test_connection()