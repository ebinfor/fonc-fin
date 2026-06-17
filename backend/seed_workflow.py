import asyncio
import uuid
from app.core.database import AsyncSessionLocal
from app.models.workflow_engine import WorkflowDefinition, WorkflowStepDef

async def seed_data():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            def_id = uuid.UUID("de305d54-75b4-431b-adb2-eb6b9e546013")
            
            # 1. Création de la définition attendue par ton curl
            definition = WorkflowDefinition(
                id=def_id,
                code="ANNF_STANDARD",
                nom="Validation standard des dossiers ANNF",
                type_workflow="ANNF",
                description="Workflow institutionnel ANNF — Version 2026",
                is_active=True
            )
            
            # 2. Création de la première étape obligatoire (ordre == 1)
            # Le rôle requis est ADMIN car c'est le rôle présent dans ton Token JWT
            etape1 = WorkflowStepDef(
                id=uuid.uuid4(),
                definition_id=def_id,
                ordre=1,
                code_etape="VERIFIER",
                nom_etape="Vérification documentaire technique",
                role_requis="ADMIN",
                role_backup="SUPERVISEUR"
            )
            
            session.add(definition)
            session.add(etape1)
            
        print("🎉 Définition ANNF et Étape 1 injectées avec succès sur Railway !")

if __name__ == "__main__":
    asyncio.run(seed_data())