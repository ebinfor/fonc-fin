from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional  # <--- Correction ici : Import manquant ajouté

# Reste de tes imports existants (ex: de app.core.database, app.schemas, etc.)
# ...

router = APIRouter()

# Tes routes de gestion du Registre National des Personnes (RNP)
# ...