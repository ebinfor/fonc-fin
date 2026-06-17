"""parcellaire antifraude

Revision ID: 003
Revises: 002
Create Date: 2025-11-15 14:30:22.123456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
# Identifiants de révision Alembic réels
revision: str = '003_parcellaire_antifraude'
down_revision: Union[str, None] = '002_plan_directeur'
revision: str = '003_parcellaire_antifraude'
down_revision: Union[str, None] = '002_plan_directeur'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Récupération de l'état actuel de la base de données de test/dev
    connection = op.get_bind()
    inspector = inspect(connection)
    existing_tables = inspector.get_table_names()

    # 2. 👤 SÉCURITÉ USER : Création de la table 'users' si elle n'existe pas encore
    if "users" not in existing_tables:
        op.execute(sa.text("""
            CREATE TABLE users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        print("✅ Table de secours 'users' créée avec succès.")

    # 3. 🏢 SÉCURITÉ COMMUNE : Vérification de la dépendance 'communes'
    # Si la table 'communes' n'existe pas, on la crée rapidement pour la clé étrangère
    if "communes" not in existing_tables:
        op.create_table(
            'communes',
            sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
            sa.Column('code_commune', sa.String(length=10), nullable=False),
            sa.Column('nom_commune', sa.String(length=100), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code_commune')
        )
        print("✅ Table de secours 'communes' créée pour les contraintes de clés étrangères.")

    # 4. 📜 TABLE ARRÊTÉS URBANISME : Création unique et contrôlée
    if "arretes_urbanisme" not in existing_tables:
        op.create_table(
            'arretes_urbanisme',
            sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
            sa.Column('commune_id', sa.UUID(), nullable=False),
            sa.Column('numero_arrete', sa.String(length=100), nullable=False),
            sa.Column('code_arrete', sa.String(length=7), nullable=False),
            sa.Column('date_signature', sa.DateTime(timezone=True), nullable=False),
            sa.Column('objet', sa.Text(), nullable=True),
            sa.Column('statut', sa.String(length=20), server_default='actif', nullable=True),
            sa.Column('sha256_document', sa.String(length=64), nullable=True),
            sa.Column('signe_par', sa.UUID(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
            
            # Définition des clés étrangères
            sa.ForeignKeyConstraint(['commune_id'], ['communes.id'], name='fk_arretes_commune'),
            sa.ForeignKeyConstraint(['signe_par'], ['users.id'], name='fk_arretes_user'),
            
            # Contraintes d'intégrité
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code_arrete', name='uq_arrete_code'),
            sa.UniqueConstraint('numero_arrete', name='uq_arrete_numero')
        )
        print("✅ Table 'arretes_urbanisme' initialisée proprement.")
    else:
        print("ℹ️ La table 'arretes_urbanisme' est déjà présente. Étape ignorée.")

    # 5. 🗺️ AUTRES TABLES DU MODULE PARCELLAIRE (Exemple : Parcelles)
    if "parcelles" not in existing_tables:
        op.create_table(
            'parcelles',
            sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
            sa.Column('numero_parcelle', sa.String(length=50), nullable=False),
            sa.Column('commune_id', sa.UUID(), nullable=False),
            sa.Column('proprietaire_id', sa.UUID(), nullable=True),
            sa.Column('superficie', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
            sa.ForeignKeyConstraint(['commune_id'], ['communes.id'], name='fk_parcelles_commune'),
            sa.PrimaryKeyConstraint('id')
        )
        print("✅ Table 'parcelles' initialisée proprement.")


def downgrade() -> None:
    # Suppression dans l'ordre inverse des dépendances pour éviter les violations de clés étrangères
    op.drop_table('parcelles')
    op.drop_table('arretes_urbanisme')
    # On évite de supprimer 'users' et 'communes' si d'autres modules s'appuient dessus