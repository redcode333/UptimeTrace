"""infrastructure inventory: environments, regions, clusters, services and their relationships

Revision ID: 0008
Revises: 0007

Design notes (see docs/INFRASTRUCTURE.md):
  * Every grouping row carries `workspace_id`. Every relationship is a COMPOSITE foreign key on (workspace_id, id), so a row can
    only reference a row of the same workspace, enforced by the database and not only by the API.
  * Grouping is optional: a region may have no environment, a cluster no region, a host no cluster, a service no host.
  * Deleting a grouping never deletes hosts, telemetry, alerts or investigations. `ON DELETE SET NULL (column)` detaches only the
    reference column (PostgreSQL 15+); `service_hosts` rows are the only rows removed when a service is deleted.
  * Same access layers as 0003/0006: RLS on, no grants for the public Data-API roles, and the application role gets exactly the
    privileges it needs. The application role may change hosts.cluster_id and nothing else on hosts.
  * `NULLS NOT DISTINCT` makes names unique within the parent even when the parent is absent (for example two unassigned regions).
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"
NEW_TABLES = ("environments", "regions", "clusters", "services", "service_hosts")


def _workspace_column():
    return sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False)


def _audit_columns():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _workspace_column(),
        sa.Column("name", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("workspace_id", "id", name="uq_environments_ws_id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_environments_ws_name"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_environments_name"),
    )
    op.create_table(
        "regions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _workspace_column(),
        sa.Column("environment_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("workspace_id", "id", name="uq_regions_ws_id"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_regions_name"),
    )
    op.execute("ALTER TABLE public.regions ADD CONSTRAINT fk_regions_environment FOREIGN KEY (workspace_id, environment_id) "
               "REFERENCES public.environments (workspace_id, id) ON DELETE SET NULL (environment_id)")
    op.execute("CREATE UNIQUE INDEX uq_regions_ws_env_name ON public.regions (workspace_id, environment_id, name) NULLS NOT DISTINCT")

    op.create_table(
        "clusters",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _workspace_column(),
        sa.Column("region_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("workspace_id", "id", name="uq_clusters_ws_id"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_clusters_name"),
    )
    op.execute("ALTER TABLE public.clusters ADD CONSTRAINT fk_clusters_region FOREIGN KEY (workspace_id, region_id) "
               "REFERENCES public.regions (workspace_id, id) ON DELETE SET NULL (region_id)")
    op.execute("CREATE UNIQUE INDEX uq_clusters_ws_region_name ON public.clusters (workspace_id, region_id, name) NULLS NOT DISTINCT")

    # hosts: a unique (workspace_id, id) target for composite foreign keys, and the optional cluster reference.
    op.execute("ALTER TABLE public.hosts ADD CONSTRAINT uq_hosts_ws_id UNIQUE (workspace_id, id)")
    op.add_column("hosts", sa.Column("cluster_id", pg.UUID(as_uuid=True), nullable=True))
    op.execute("ALTER TABLE public.hosts ADD CONSTRAINT fk_hosts_cluster FOREIGN KEY (workspace_id, cluster_id) "
               "REFERENCES public.clusters (workspace_id, id) ON DELETE SET NULL (cluster_id)")
    op.create_index("ix_hosts_workspace_cluster", "hosts", ["workspace_id", "cluster_id"])

    op.create_table(
        "services",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _workspace_column(),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint("workspace_id", "id", name="uq_services_ws_id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_services_ws_name"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_services_name"),
    )

    op.create_table(
        "service_hosts",
        _workspace_column(),
        sa.Column("service_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("host_pk", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("service_id", "host_pk", name="pk_service_hosts"),
    )
    op.execute("ALTER TABLE public.service_hosts ADD CONSTRAINT fk_service_hosts_service FOREIGN KEY (workspace_id, service_id) "
               "REFERENCES public.services (workspace_id, id) ON DELETE CASCADE")
    op.execute("ALTER TABLE public.service_hosts ADD CONSTRAINT fk_service_hosts_host FOREIGN KEY (workspace_id, host_pk) "
               "REFERENCES public.hosts (workspace_id, id) ON DELETE CASCADE")
    op.create_index("ix_service_hosts_host", "service_hosts", ["host_pk"])

    # --- access model (same layers as 0003/0006) -----------------------------------------------------------------
    for table in NEW_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"""
            DO $$
            DECLARE r text;
            BEGIN
                FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN {PUBLIC_API_ROLES} LOOP
                    EXECUTE format('REVOKE ALL ON TABLE public.{table} FROM %I', r);
                END LOOP;
            END $$
        """)
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    grants = {
        "environments": "SELECT, INSERT, UPDATE, DELETE",
        "regions": "SELECT, INSERT, UPDATE, DELETE",
        "clusters": "SELECT, INSERT, UPDATE, DELETE",
        "services": "SELECT, INSERT, UPDATE, DELETE",
        "service_hosts": "SELECT, INSERT, DELETE",
    }
    for table, privs in grants.items():
        op.execute(f"GRANT {privs} ON public.{table} TO uptimetrace_app")
        for verb in ("select", "insert", "update", "delete"):
            if verb.upper() in privs:
                clause = "USING (true)" if verb in ("select", "update", "delete") else ""
                wc = "WITH CHECK (true)" if verb in ("insert", "update") else ""
                op.execute(f"CREATE POLICY {table}_app_{verb} ON public.{table} FOR {verb.upper()} TO uptimetrace_app {clause} {wc}")
    # The application may change the cluster a host belongs to, and nothing else new on hosts.
    op.execute("GRANT UPDATE (cluster_id) ON public.hosts TO uptimetrace_app")


def downgrade() -> None:
    op.drop_index("ix_service_hosts_host", table_name="service_hosts")
    op.drop_table("service_hosts")
    op.drop_table("services")
    op.drop_index("ix_hosts_workspace_cluster", table_name="hosts")
    op.execute("ALTER TABLE public.hosts DROP CONSTRAINT IF EXISTS fk_hosts_cluster")
    op.drop_column("hosts", "cluster_id")
    op.execute("ALTER TABLE public.hosts DROP CONSTRAINT IF EXISTS uq_hosts_ws_id")
    op.drop_table("clusters")
    op.drop_table("regions")
    op.drop_table("environments")
