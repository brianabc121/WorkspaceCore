"""Frozen canonical main d4e0a4b 0069/0070 PostgreSQL16 schema, independent of app models."""

from typing import Any

CHAT_REQUEST_UNIQUE = "uq_llm_bridge_chat_groups_workspace_id_created_by_request_id"

CANONICAL_TABLES: dict[str, dict[str, Any]] = {
    "llm_bridge_chat_groups": {
        "columns": [
            ["id", "UUID", False, "gen_random_uuid()"],
            ["workspace_id", "UUID", False, None],
            ["title", "TEXT", False, None],
            ["created_by", "UUID", True, None],
            ["created_by_name", "TEXT", False, "''::text"],
            ["request_id", "TEXT", False, None],
            ["status", "TEXT", False, "'active'::text"],
            ["created_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
            ["updated_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
        ],
        "pk": {
            "constrained_columns": ["id"],
            "name": "pk_llm_bridge_chat_groups",
            "dialect_options": {"postgresql_include": []},
        },
        "unique": [
            {
                "column_names": ["workspace_id", "created_by", "request_id"],
                "name": CHAT_REQUEST_UNIQUE,
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            }
        ],
        "foreign": [
            {
                "name": "fk_llm_bridge_chat_groups_created_by_users",
                "constrained_columns": ["created_by"],
                "referred_schema": "workspace",
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_chat_groups_workspace_id_workspaces",
                "constrained_columns": ["workspace_id"],
                "referred_schema": "workspace",
                "referred_table": "workspaces",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ],
        "indexes": [
            {
                "name": "ix_llm_bridge_chat_groups_listing",
                "unique": False,
                "column_names": ["workspace_id", "status", "updated_at"],
                "dialect_options": {"postgresql_include": []},
            },
            {
                "name": CHAT_REQUEST_UNIQUE,
                "unique": True,
                "column_names": ["workspace_id", "created_by", "request_id"],
                "duplicates_constraint": CHAT_REQUEST_UNIQUE,
                "dialect_options": {"postgresql_include": []},
            },
        ],
        "checks": [],
    },
    "llm_bridge_chat_group_members": {
        "columns": [
            ["id", "UUID", False, "gen_random_uuid()"],
            ["group_id", "UUID", False, None],
            ["position", "INTEGER", False, None],
            ["bridge_id", "UUID", True, None],
            ["bridge_name", "TEXT", False, None],
            ["platform", "TEXT", False, None],
            ["mode", "TEXT", False, None],
            ["model", "TEXT", True, None],
            ["session_id", "TEXT", True, None],
            ["error", "TEXT", False, "''::text"],
        ],
        "pk": {
            "constrained_columns": ["id"],
            "name": "pk_llm_bridge_chat_group_members",
            "dialect_options": {"postgresql_include": []},
        },
        "unique": [
            {
                "column_names": ["group_id", "position"],
                "name": "uq_llm_bridge_chat_group_members_group_id_position",
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            }
        ],
        "foreign": [
            {
                "name": "fk_llm_bridge_chat_group_members_bridge_id_llm_bridge_bridges",
                "constrained_columns": ["bridge_id"],
                "referred_schema": "workspace",
                "referred_table": "llm_bridge_bridges",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_chat_group_members_group",
                "constrained_columns": ["group_id"],
                "referred_schema": "workspace",
                "referred_table": "llm_bridge_chat_groups",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ],
        "indexes": [
            {
                "name": "uq_llm_bridge_chat_group_members_group_id_position",
                "unique": True,
                "column_names": ["group_id", "position"],
                "duplicates_constraint": "uq_llm_bridge_chat_group_members_group_id_position",
                "dialect_options": {"postgresql_include": []},
            }
        ],
        "checks": [],
    },
    "llm_bridge_installs": {
        "columns": [
            ["id", "UUID", False, "gen_random_uuid()"],
            ["workspace_id", "UUID", False, None],
            ["bridge_id", "UUID", False, None],
            ["target_kind", "TEXT", False, None],
            ["server_id", "UUID", True, None],
            ["runner_host_id", "UUID", True, None],
            ["server_name", "TEXT", False, None],
            ["address", "TEXT", False, None],
            ["ssh_port", "INTEGER", False, None],
            ["username", "TEXT", False, None],
            ["host_key", "TEXT", False, None],
            ["slot", "TEXT", False, None],
            ["install_dir", "TEXT", False, None],
            ["mode", "TEXT", False, None],
            ["port", "INTEGER", True, None],
            ["tunnel_key_ref", "TEXT", True, None],
            ["tunnel_public_key", "TEXT", True, None],
            ["platforms", "JSONB", False, "'[]'::jsonb"],
            ["source_tag", "TEXT", True, None],
            ["needs_sudo", "BOOLEAN", False, "false"],
            ["deployed_by", "UUID", True, None],
            ["deployed_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
            ["updated_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
        ],
        "pk": {
            "constrained_columns": ["id"],
            "name": "pk_llm_bridge_installs",
            "dialect_options": {"postgresql_include": []},
        },
        "unique": [
            {
                "column_names": ["bridge_id"],
                "name": "uq_llm_bridge_installs_bridge_id",
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            }
        ],
        "foreign": [
            {
                "name": "fk_llm_bridge_installs_bridge_id_llm_bridge_bridges",
                "constrained_columns": ["bridge_id"],
                "referred_schema": "workspace",
                "referred_table": "llm_bridge_bridges",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
            {
                "name": "fk_llm_bridge_installs_deployed_by_users",
                "constrained_columns": ["deployed_by"],
                "referred_schema": "workspace",
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_installs_runner_host_id_runner_hosts",
                "constrained_columns": ["runner_host_id"],
                "referred_schema": "workspace",
                "referred_table": "runner_hosts",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_installs_server_id_agent_servers",
                "constrained_columns": ["server_id"],
                "referred_schema": "workspace",
                "referred_table": "agent_servers",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_installs_workspace_id_workspaces",
                "constrained_columns": ["workspace_id"],
                "referred_schema": "workspace",
                "referred_table": "workspaces",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ],
        "indexes": [
            {
                "name": "ix_llm_bridge_installs_runner_host",
                "unique": False,
                "column_names": ["workspace_id", "runner_host_id"],
                "dialect_options": {"postgresql_include": []},
            },
            {
                "name": "ix_llm_bridge_installs_server",
                "unique": False,
                "column_names": ["workspace_id", "server_id"],
                "dialect_options": {"postgresql_include": []},
            },
            {
                "name": "uq_llm_bridge_installs_bridge_id",
                "unique": True,
                "column_names": ["bridge_id"],
                "duplicates_constraint": "uq_llm_bridge_installs_bridge_id",
                "dialect_options": {"postgresql_include": []},
            },
        ],
        "checks": [],
    },
    "llm_bridge_deploy_jobs": {
        "columns": [
            ["id", "UUID", False, "gen_random_uuid()"],
            ["workspace_id", "UUID", False, None],
            ["kind", "TEXT", False, None],
            ["status", "TEXT", False, "'running'::text"],
            ["target_kind", "TEXT", False, None],
            ["server_id", "UUID", True, None],
            ["runner_host_id", "UUID", True, None],
            ["server_name", "TEXT", False, None],
            ["bridge_id", "UUID", True, None],
            ["name", "TEXT", False, None],
            ["platforms", "JSONB", False, "'[]'::jsonb"],
            ["slot", "TEXT", False, None],
            ["mode", "TEXT", True, None],
            ["install_dir", "TEXT", True, None],
            ["port", "INTEGER", True, None],
            ["request_id", "TEXT", False, None],
            ["requested_by", "UUID", True, None],
            ["steps", "JSONB", False, "'[]'::jsonb"],
            ["log", "TEXT", False, "''::text"],
            ["error_code", "TEXT", True, None],
            ["error_message", "TEXT", True, None],
            ["started_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
            ["updated_at", "TIMESTAMP WITH TIME ZONE", False, "now()"],
            ["finished_at", "TIMESTAMP WITH TIME ZONE", True, None],
        ],
        "pk": {
            "constrained_columns": ["id"],
            "name": "pk_llm_bridge_deploy_jobs",
            "dialect_options": {"postgresql_include": []},
        },
        "unique": [
            {
                "column_names": ["workspace_id", "request_id"],
                "name": "uq_llm_bridge_deploy_jobs_workspace_id_request_id",
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            }
        ],
        "foreign": [
            {
                "name": "fk_llm_bridge_deploy_jobs_bridge_id_llm_bridge_bridges",
                "constrained_columns": ["bridge_id"],
                "referred_schema": "workspace",
                "referred_table": "llm_bridge_bridges",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_deploy_jobs_requested_by_users",
                "constrained_columns": ["requested_by"],
                "referred_schema": "workspace",
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_deploy_jobs_runner_host_id_runner_hosts",
                "constrained_columns": ["runner_host_id"],
                "referred_schema": "workspace",
                "referred_table": "runner_hosts",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_deploy_jobs_server_id_agent_servers",
                "constrained_columns": ["server_id"],
                "referred_schema": "workspace",
                "referred_table": "agent_servers",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": "fk_llm_bridge_deploy_jobs_workspace_id_workspaces",
                "constrained_columns": ["workspace_id"],
                "referred_schema": "workspace",
                "referred_table": "workspaces",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ],
        "indexes": [
            {
                "name": "ix_llm_bridge_deploy_jobs_listing",
                "unique": False,
                "column_names": ["workspace_id", "started_at"],
                "dialect_options": {"postgresql_include": []},
            },
            {
                "name": "uq_llm_bridge_deploy_jobs_running",
                "unique": True,
                "column_names": ["server_id"],
                "dialect_options": {
                    "postgresql_where": "(status = 'running'::text)",
                    "postgresql_include": [],
                },
            },
            {
                "name": "uq_llm_bridge_deploy_jobs_running_runner",
                "unique": True,
                "column_names": ["runner_host_id"],
                "dialect_options": {
                    "postgresql_where": "(status = 'running'::text)",
                    "postgresql_include": [],
                },
            },
            {
                "name": "uq_llm_bridge_deploy_jobs_workspace_id_request_id",
                "unique": True,
                "column_names": ["workspace_id", "request_id"],
                "duplicates_constraint": "uq_llm_bridge_deploy_jobs_workspace_id_request_id",
                "dialect_options": {"postgresql_include": []},
            },
        ],
        "checks": [],
    },
}
