"""
app/db/init_db.py
Database initialisation script.

Run once before starting the application for the first time:
    python -m app.db.init_db

This script:
  1. Creates all SQLAlchemy ORM tables (runs, etc.)
  2. Verifies PostgreSQL connectivity
  3. Prints a confirmation table listing

LangGraph's checkpoint tables (checkpoints, checkpoint_writes) are created
separately by AsyncPostgresSaver.setup() during app startup — NOT here.
"""

import asyncio
import logging
import sys

from sqlalchemy import inspect, text

from app.db.database import Base, create_all_tables, db_session, engine

# Import all models so SQLAlchemy's metadata is populated
from app.db.models import Run  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def verify_connection() -> bool:
    """Verify we can reach PostgreSQL before running DDL."""
    try:
        async with db_session() as session:
            result = await session.execute(text("SELECT version()"))
            version = result.scalar()
            logger.info(f"PostgreSQL connection OK — {version}")
            return True
    except Exception as exc:
        logger.error(f"Cannot connect to PostgreSQL: {exc}")
        logger.error(
            "Ensure PostgreSQL is running and CHECKPOINT_DB_URI / DATABASE_URL are correct in .env"
        )
        return False


async def create_tables() -> None:
    """Create all ORM tables — idempotent (CREATE TABLE IF NOT EXISTS)."""
    logger.info("Creating application tables...")
    await create_all_tables()
    logger.info("Tables created (or already exist).")


async def list_tables() -> list[str]:
    """List all tables currently in the database."""
    tables = []
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' "
                "ORDER BY tablename"
            )
        )
        tables = [row[0] for row in result.fetchall()]
    return tables


async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Workflow Orchestrator — Database Initialisation")
    logger.info("=" * 60)

    # Step 1: Verify connectivity
    ok = await verify_connection()
    if not ok:
        sys.exit(1)

    # Step 2: Create ORM tables
    await create_tables()

    # Step 3: Report what exists
    tables = await list_tables()
    logger.info(f"Tables in database ({len(tables)} total):")
    for t in tables:
        logger.info(f"  ✓ {t}")

    logger.info("=" * 60)
    logger.info("Database initialisation complete.")
    logger.info(
        "NOTE: LangGraph checkpoint tables are created at app startup "
        "by AsyncPostgresSaver.setup() — they will appear after first run."
    )
    logger.info("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
