import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Si DATABASE_URL est dans Railway, il l'utilisera. 
# Sinon, il créera un fichier SQLite local pour le développement.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./foncier_plus.db")

# Configuration engine
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()