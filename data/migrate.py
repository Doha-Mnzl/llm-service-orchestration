"""Applique les migrations SQL versionnées des services de la Partie 3.

Chaque service possède ses migrations : services/<service>/migrations/NNN_nom.sql.
Les migrations appliquées sont tracées dans public.schema_migrations (service, version,
checksum) : relancer la commande ne rejoue rien, et une migration déjà appliquée puis
modifiée est signalée (MigrationError) au lieu d'être réappliquée en silence.

    python -m data.migrate                     # DATABASE_URL ou --database-url
    python -m data.migrate --reset             # supprime les 3 schémas puis migre (dev/test)

Garde-fou : --reset est REFUSÉ s'il existe des décisions humaines (source autre que « seed »),
sauf avec --allow-decision-loss. Une migration ordinaire ne supprime jamais de données.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
# Ordre d'application : aucun schéma ne dépend d'un autre, l'ordre est seulement stable.
SERVICES = ("catalog", "sales", "inventory")
DEFAULT_URL = "postgresql://lso:lso@localhost:5432/lso"
MIGRATION_LOCK = "lso-partie3-migrations"  # libéré à la déconnexion (fin du bloc with)


class MigrationError(RuntimeError):
    pass


def _files(service: str) -> list[Path]:
    return sorted((ROOT / "services" / service / "migrations").glob("[0-9][0-9][0-9]_*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(database_url: str) -> list[str]:
    """Applique les migrations manquantes. Renvoie la liste « service/fichier » appliquée."""
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        # Verrou consultatif de session : deux démarrages simultanés appliquent les migrations l'un
        # après l'autre ; le second relit le registre et ne rejoue rien (revue Codex, règle lock-advisory).
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (MIGRATION_LOCK,))
        conn.execute("""CREATE TABLE IF NOT EXISTS public.schema_migrations (
                            service TEXT NOT NULL, version TEXT NOT NULL, checksum TEXT NOT NULL,
                            applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (service, version))""")
        done = {(s, v): c for s, v, c in conn.execute(
            "SELECT service, version, checksum FROM public.schema_migrations")}
        for service in SERVICES:
            for path in _files(service):
                key, checksum = (service, path.name), _checksum(path)
                if key in done:
                    if done[key] != checksum:
                        raise MigrationError(f"{service}/{path.name} a été modifiée après application "
                                             "(créer une nouvelle migration au lieu de modifier l'ancienne)")
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO public.schema_migrations (service, version, checksum) "
                                 "VALUES (%s, %s, %s)", (service, path.name, checksum))
                applied_now.append(f"{service}/{path.name}")
    return applied_now


def human_decisions(conn: psycopg.Connection) -> int:
    """Nombre de décisions humaines (hors démonstration) présentes dans la base."""
    if conn.execute("SELECT to_regclass('catalog.decisions')").fetchone()[0] is None:
        return 0
    return conn.execute("SELECT count(*) FROM catalog.decisions WHERE source <> 'seed'").fetchone()[0]


def reset(database_url: str, allow_decision_loss: bool = False) -> None:
    """Supprime les schémas de la Partie 3 (développement et tests uniquement).

    Refuse d'effacer des décisions humaines sans accord explicite (allow_decision_loss=True).
    """
    with psycopg.connect(database_url) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MIGRATION_LOCK,))  # jamais pendant une migration
        if conn.execute("SELECT to_regclass('catalog.decisions')").fetchone()[0] is not None:
            # verrou exclusif AVANT le comptage : aucune décision ne peut être validée entre les deux
            conn.execute("LOCK TABLE catalog.decisions IN ACCESS EXCLUSIVE MODE")
        count = human_decisions(conn)
        if count and not allow_decision_loss:
            raise MigrationError(f"{count} décision(s) humaine(s) seraient effacées : réinitialisation refusée "
                                 "(option --allow-decision-loss pour forcer, après sauvegarde)")
        for service in SERVICES:
            conn.execute(f"DROP SCHEMA IF EXISTS {service} CASCADE")
        conn.execute("DROP TABLE IF EXISTS public.schema_migrations")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", DEFAULT_URL))
    p.add_argument("--reset", action="store_true", help="supprimer les schémas avant de migrer")
    p.add_argument("--allow-decision-loss", action="store_true",
                   help="autoriser --reset à effacer des décisions humaines (irréversible)")
    a = p.parse_args(argv)
    try:
        if a.reset:
            reset(a.database_url, a.allow_decision_loss)
        applied = migrate(a.database_url)
    except (psycopg.Error, MigrationError) as e:
        print(f"ÉCHEC : {e}", file=sys.stderr)
        return 1
    print("\n".join(f"appliquée : {m}" for m in applied) or "Base à jour : aucune migration à appliquer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
