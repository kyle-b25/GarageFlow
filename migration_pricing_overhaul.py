"""
Pricing Overhaul Migration
==========================
Run this with: python migration_pricing_overhaul.py

Or use Flask-Migrate:
  flask db migrate -m "pricing overhaul"
  flask db upgrade

Manual Alembic migration for use if auto-generation doesn't cover the
column drops cleanly (common with SQLite).

Changes:
  garage       — ADD COLUMN base_rate_per_hour NUMERIC(10,2) DEFAULT 2.00
  pricing_rule — ADD COLUMN rate_per_hour NUMERIC(10,2)
  pricing_rule — DROP COLUMN pricing_model
  pricing_rule — DROP COLUMN program

Uses batch mode (table-copy strategy) so it works on SQLite, which does
not support DROP COLUMN natively in versions older than 3.35.
"""

import os
import sys

# Add project root to path so app/db can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run():
    from app import app, db
    from sqlalchemy import text, inspect

    with app.app_context():
        engine = db.engine
        inspector = inspect(engine)
        garage_cols = {c['name'] for c in inspector.get_columns('garage')}
        rule_cols = {c['name'] for c in inspector.get_columns('pricing_rule')}

        with engine.begin() as conn:

            # --- garage: add base_rate_per_hour ---
            if 'base_rate_per_hour' not in garage_cols:
                print("Adding base_rate_per_hour to garage...")
                conn.execute(text(
                    "ALTER TABLE garage ADD COLUMN base_rate_per_hour NUMERIC(10,2) NOT NULL DEFAULT 2.00"
                ))
                print("  Done.")
            else:
                print("garage.base_rate_per_hour already exists — skipping.")

            # --- pricing_rule: add rate_per_hour ---
            if 'rate_per_hour' not in rule_cols:
                print("Adding rate_per_hour to pricing_rule...")
                conn.execute(text(
                    "ALTER TABLE pricing_rule ADD COLUMN rate_per_hour NUMERIC(10,2) NOT NULL DEFAULT 2.00"
                ))
                print("  Done.")
            else:
                print("pricing_rule.rate_per_hour already exists — skipping.")

        # --- pricing_rule: drop program and pricing_model via table recreation ---
        cols_to_drop = {'program', 'pricing_model'} & rule_cols
        if cols_to_drop:
            print(f"Dropping columns {cols_to_drop} from pricing_rule (via table recreation)...")
            _drop_columns_sqlite(engine, 'pricing_rule', cols_to_drop)
            print("  Done.")
        else:
            print("pricing_rule.program / .pricing_model already gone — skipping.")

        print("\nMigration complete.")
        print("Remaining pricing_rule columns:", [
            c['name'] for c in inspect(engine).get_columns('pricing_rule')
        ])


def _drop_columns_sqlite(engine, table_name, cols_to_drop):
    """
    Recreate a SQLite table without the specified columns.
    This is Alembic's 'batch mode' approach done manually.
    """
    from sqlalchemy import text, inspect, MetaData, Table

    meta = MetaData()
    meta.reflect(bind=engine)
    old_table = meta.tables[table_name]

    keep_cols = [c for c in old_table.columns if c.name not in cols_to_drop]
    keep_names = [c.name for c in keep_cols]
    keep_names_sql = ', '.join(keep_names)

    tmp = f"_tmp_{table_name}"

    col_defs = []
    for c in keep_cols:
        t = str(c.type)
        nn = ' NOT NULL' if not c.nullable else ''
        pk = ' PRIMARY KEY AUTOINCREMENT' if c.primary_key else ''
        default = f' DEFAULT {c.server_default.arg}' if c.server_default else ''
        unique = ' UNIQUE' if c.unique else ''
        col_defs.append(f'  {c.name} {t}{pk}{nn}{default}{unique}')
    col_defs_sql = ',\n'.join(col_defs)

    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE {tmp} (\n{col_defs_sql}\n)'))
        conn.execute(text(
            f'INSERT INTO {tmp} ({keep_names_sql}) SELECT {keep_names_sql} FROM {table_name}'
        ))
        conn.execute(text(f'DROP TABLE {table_name}'))
        conn.execute(text(f'ALTER TABLE {tmp} RENAME TO {table_name}'))


if __name__ == '__main__':
    run()
