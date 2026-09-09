"""Unit tests for PostgreSQL storage schema and Alembic migration correctness."""
from pathlib import Path
import io
import pytest

from infrastructure.storage.postgres.database import Base
import infrastructure.storage.postgres.schema  # noqa: F401 - registers tables on Base.metadata

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic import command


EXPECTED_TABLES = {
    "symbols",
    "market_snapshots",
    "signals",
    "risk_decisions",
    "orders",
    "audit_logs",
    "trade_journals",
}


def test_postgres_metadata_registers_all_tables() -> None:
    """Verify that the DeclarativeBase metadata has all canonical schema tables registered."""
    registered_tables = set(Base.metadata.tables.keys())
    assert EXPECTED_TABLES.issubset(registered_tables), (
        f"Missing expected tables in metadata: {EXPECTED_TABLES - registered_tables}"
    )


def test_alembic_script_directory_and_head_revision() -> None:
    """Verify alembic config resolves the migration script directory and head revision."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    ini_path = repo_root / "infrastructure" / "storage" / "postgres" / "alembic.ini"
    assert ini_path.is_file(), f"alembic.ini not found at {ini_path}"

    cfg = Config(str(ini_path))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 migration head, got: {heads}"
    assert heads[0] == "fb91b07df6bf", f"Expected head fb91b07df6bf, got {heads[0]}"


def test_alembic_offline_sql_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Alembic upgrade and downgrade run in offline SQL mode without errors."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    ini_path = repo_root / "infrastructure" / "storage" / "postgres" / "alembic.ini"
    cfg = Config(str(ini_path))

    upgrade_buf = io.StringIO()
    downgrade_buf = io.StringIO()

    # Capture stdout during offline upgrade --sql
    with monkeypatch.context() as m:
        m.setattr("sys.stdout", upgrade_buf)
        command.upgrade(cfg, "head", sql=True)

    upgrade_sql = upgrade_buf.getvalue()
    assert "CREATE TABLE symbols" in upgrade_sql
    assert "CREATE TABLE market_snapshots" in upgrade_sql
    assert "CREATE TABLE signals" in upgrade_sql
    assert "CREATE TABLE risk_decisions" in upgrade_sql
    assert "CREATE TABLE orders" in upgrade_sql
    assert "CREATE TABLE audit_logs" in upgrade_sql
    assert "CREATE TABLE trade_journals" in upgrade_sql

    # Capture stdout during offline downgrade --sql
    with monkeypatch.context() as m:
        m.setattr("sys.stdout", downgrade_buf)
        command.downgrade(cfg, "fb91b07df6bf:base", sql=True)

    downgrade_sql = downgrade_buf.getvalue()
    assert "DROP TABLE symbols" in downgrade_sql
    assert "DROP TABLE trade_journals" in downgrade_sql
    assert "DROP TABLE risk_decisions" in downgrade_sql
