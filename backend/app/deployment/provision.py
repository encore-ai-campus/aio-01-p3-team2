"""Provision dedicated PostgreSQL database owners and non-inheriting login roles."""

import argparse
import json
import os

from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy import create_engine, text

from backend.app.config.cloud_database import DATABASES, CloudDatabaseError, validate_rds_url
from backend.app.config.deployment import deployment_mode


def provision_cluster(engine, passwords: dict[str, str], *, specs=None) -> None:
    specs = list(DATABASES.values()) if specs is None else specs
    roles = [name for spec in specs for name in (spec.owner, spec.runtime, spec.reader)]
    if set(passwords) != set(roles) or any(
        not isinstance(value, str) or len(value) < 32 for value in passwords.values()
    ):
        raise ValueError(
            "Each dedicated login requires an injected password of at least 32 characters"
        )
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("Dedicated logins require distinct passwords")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for role in roles:
            row = connection.execute(
                text("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,
                rolreplication, rolinherit, rolcanlogin,
                EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
                FROM pg_roles WHERE rolname=:role"""),
                {"role": role},
            ).first()
            if row is None:
                # Keep passwords out of SQLAlchemy statements, SQL logs and exceptions.
                try:
                    with connection.connection.driver_connection.cursor() as cursor:
                        cursor.execute(
                            sql.SQL(
                                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                            ).format(sql.Identifier(role), sql.Literal(passwords[role]))
                        )
                except PsycopgError:
                    raise CloudDatabaseError("Dedicated database role creation failed") from None
            elif any(row[:6]) or not row[6] or row[7]:
                raise CloudDatabaseError(
                    "Existing database role has unexpected privileges or memberships"
                )
        admin = connection.execute(text("SELECT current_user")).scalar_one()
        for spec in specs:
            row = connection.execute(
                text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=:db"),
                {"db": spec.database},
            ).first()
            if row is not None and row[0] != spec.owner:
                raise CloudDatabaseError("Existing database has an unexpected owner")
            can_set = connection.execute(
                text("SELECT pg_has_role(current_user, :owner, 'SET')"), {"owner": spec.owner}
            ).scalar_one()
            direct = connection.execute(
                text("""SELECT m.set_option FROM pg_auth_members m
                JOIN pg_roles owner ON m.roleid=owner.oid JOIN pg_roles member ON m.member=member.oid
                WHERE owner.rolname=:owner AND member.rolname=current_user"""),
                {"owner": spec.owner},
            ).first()
            role_sql = sql.Identifier(spec.owner)
            admin_sql = sql.Identifier(admin)
            try:
                if not can_set:
                    connection.exec_driver_sql(
                        sql.SQL("GRANT {} TO {} WITH SET TRUE")
                        .format(role_sql, admin_sql)
                        .as_string()
                    )
                if row is None:
                    connection.exec_driver_sql(
                        sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0")
                        .format(sql.Identifier(spec.database), role_sql)
                        .as_string()
                    )
                # Database ACL changes require the actual owner, even for a CREATEDB/CREATEROLE bootstrap login.
                connection.exec_driver_sql(sql.SQL("SET ROLE {}").format(role_sql).as_string())
                connection.exec_driver_sql(
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC")
                    .format(sql.Identifier(spec.database))
                    .as_string()
                )
                for role in roles:
                    if role != spec.owner:
                        connection.exec_driver_sql(
                            sql.SQL("REVOKE ALL ON DATABASE {} FROM {}")
                            .format(sql.Identifier(spec.database), sql.Identifier(role))
                            .as_string()
                        )
                for role in (spec.runtime, spec.reader):
                    connection.exec_driver_sql(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                        .format(sql.Identifier(spec.database), sql.Identifier(role))
                        .as_string()
                    )
            finally:
                connection.exec_driver_sql("RESET ROLE")
                if not can_set:
                    if direct is None:
                        connection.exec_driver_sql(
                            sql.SQL("REVOKE {} FROM {}").format(role_sql, admin_sql).as_string()
                        )
                    else:
                        connection.exec_driver_sql(
                            sql.SQL("GRANT {} TO {} WITH SET FALSE")
                            .format(role_sql, admin_sql)
                            .as_string()
                        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    if deployment_mode() != "cloud":
        parser.error("Provisioning requires cloud mode")
    url = os.getenv("RDS_BOOTSTRAP_DATABASE_URL", "")
    user = os.getenv("RDS_BOOTSTRAP_USER", "")
    if not user:
        parser.error("RDS_BOOTSTRAP_USER is required")
    validate_rds_url(
        url, endpoint=os.getenv("RDS_EXPECTED_ENDPOINT", ""), database="postgres", user=user
    )
    passwords = json.loads(os.environ.get("DATABASE_ROLE_PASSWORDS", "{}"))
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        provision_cluster(engine, passwords)
        print(json.dumps({"purposes": ["main", "fault"], "provisioned": True}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
