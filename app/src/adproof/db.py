"""Database engine, session, and DB-level immutability enforcement.

Immutability is enforced by triggers rather than application convention,
because DATA_MODEL.md lists these records as immutable audit artifacts and an
application-only guarantee is not one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


#: Tables that may never be updated or deleted once a row exists.
FULLY_IMMUTABLE_TABLES = (
    "submission_version",
    "evidence_item",
    "audit_event",
    # A review or decision that can be edited afterwards is not a record of
    # what someone actually concluded. Corrections are new rows.
    "rule_review",
    "submission_decision",
)

#: Tables where a row becomes immutable once it reaches a finished state.
#: Predicates must be simple row conditions: Postgres forbids subqueries in a
#: trigger WHEN clause, so cross-table conditions live in a dedicated function
#: below instead.
CONDITIONALLY_IMMUTABLE = {
    "retrieval_run": "OLD.finished_at IS NOT NULL",
    "evaluation_result": "TRUE",
    "rule_set_version": "OLD.confirmed_at IS NOT NULL",
}

_GUARD_FN = """
CREATE OR REPLACE FUNCTION adproof_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'adproof: % on % is forbidden; % rows are immutable audit artifacts',
        TG_OP, TG_TABLE_NAME, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

#: A rule becomes immutable when its rule set is confirmed. That is a
#: cross-table condition, so it is evaluated inside the function body.
_RULE_GUARD_FN = """
CREATE OR REPLACE FUNCTION adproof_block_confirmed_rule_mutation()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM rule_set_version rsv
        WHERE rsv.id = OLD.rule_set_version_id
          AND rsv.confirmed_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'adproof: % on rule is forbidden; rules in a confirmed rule set are '
            'immutable. Create a new rule-set version instead.', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
"""


def _install_immutability_triggers(connection) -> None:
    connection.execute(text(_GUARD_FN))

    for table in FULLY_IMMUTABLE_TABLES:
        connection.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        )
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{table}_immutable "
                f"BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION adproof_block_mutation()"
            )
        )

    for table, predicate in CONDITIONALLY_IMMUTABLE.items():
        connection.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        )
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{table}_immutable "
                f"BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW WHEN ({predicate}) "
                f"EXECUTE FUNCTION adproof_block_mutation()"
            )
        )

    connection.execute(text(_RULE_GUARD_FN))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_rule_immutable ON rule"))
    connection.execute(
        text(
            "CREATE TRIGGER trg_rule_immutable "
            "BEFORE UPDATE OR DELETE ON rule "
            "FOR EACH ROW EXECUTE FUNCTION adproof_block_confirmed_rule_mutation()"
        )
    )


def init_db() -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            _install_immutability_triggers(connection)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
