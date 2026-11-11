# Runtime V2 Task 1–4 security / concurrency review

Reviewed uncommitted implementation and new files in `codex/T-0222` against `origin/main` (`a1053c7`), including shared placement, trusted agent probing, runner placement/routing, retained count, persistent capability approvals, grant activation/revoke, and relevant tests. Read the plan and linked design spec. Task 5 network is intentionally incomplete/disabled and is not assessed as a finished network implementation. No implementation edits or Git mutations were made. Other agents were editing concurrently; line numbers describe the source observed during this review and may shift.

## Findings

### Important 1: Reconciliation drops a pending restart reservation based on an old stopped container report

Location: `services/api/app/domain/runner_hosts.py:486–488`; `services/api/app/domain/runner.py:321–323`; `services/api/app/domain/runner_placement.py:20–29`.

`start()` correctly changes a stopped environment to `creating` and persists a `start_env` command so its compute is reserved before dispatch. However, `report()` unconditionally reconciles `creating`/`failed` to `stopped` when it sees an exited container, even when that environment appears in the pending/sent/unknown `busy` set. The busy check at line 495 occurs after this mutation.

Concrete trigger:
1. Existing environment is stopped. Periodic runner report observes its exited container.
2. Restart admission succeeds and stores `creating` plus a pending `start_env`.
3. That report reaches the API before the start executes and sets the environment back to `stopped` despite the pending command.
4. Another create consumes the same CPU/memory because stopped environments no longer contribute compute commitments.
5. Both start/create execute, oversubscribing the admitted capacity.

Source-only reproduction extracted and executed the actual `RunnerHosts.report` method with an environment in `creating`, the environment ID present in busy, and an `exited` container. Observed `state_after_actual_report_method='stopped'`.

Keep the reservation until an ordered runtime/allocation acknowledgment proves that the pending start/create has resolved. A container-exists report alone does not prove that a later queued start cannot execute. At minimum avoid this reconciliation while relevant lifecycle commands remain pending/sent/unknown, and test the report/start ordering.

### Important 2: Pending memory accounting uses original creation time instead of the latest compute admission

Location: `services/api/app/domain/runner_placement.py:22–29`; `services/api/app/domain/runner.py:321–324`; successful start/rebuild handling at `runner.py:1620–1626`.

Once a stopped/archived old environment restarts and becomes `running`, its pending-memory deduction depends on `env.created_at >= report.at`. Its original creation time is old, so a pre-restart free-memory report is immediately treated as if it already reflects the restarted workload. The host row lock serializes transactions but does not make that telemetry reflect the restart.

Concrete trigger reproduced with the actual `candidate_for` and `rank_candidates` functions:
- Host total memory 10 GiB; external processes consume 8 GiB; fresh report says 2 GiB free.
- Old stopped environment restarts with 1.5 GiB. Its start result arrives before the next report.
- `candidate_for` gives `pending_memory_mb=0` because its creation time is yesterday.
- A new 1.5 GiB request is admitted using the same 2 GiB report, although only 0.5 GiB remains. Platform commitments are only 3 GiB, so the 90% ledger limit does not catch external usage.

Observed source-only result: `reported_free_mb=2048`, `true_free_after_restart_mb=512`, `new_request_mb=1536`, `admitted=True`.

Persist a latest compute admission/allocation time or generation and keep post-sample restart/rebuild resources pending until a sufficiently new report confirms that allocation. Use the same mechanism for old archived restores, not just initial creates.

### Important 3: Cancellation and snapshot errors can mark a still-running workload stopped and free compute

Locations: `services/api/app/domain/runner.py:1533–1539` (`keep`), `runner.py:1690–1692` (snapshot failure), `services/api/app/domain/runner_reclaim.py:168–170` (snapshot timeout), combined with `runner_placement.py:20` and `_check_quota`'s compute-state filtering.

These existing state transitions now control resource-release authority because the new capacity/quota implementation excludes stopped environments from compute. They do not require confirmed stop completion.

Concrete agent-accessible trigger:
1. Running environment with a `released` deletion condition is released; `mark_pending_delete` queues a stop and changes state to pending_delete.
2. The owner immediately calls `env_update keep=true` while stop is pending or unknown.
3. `keep()` sets `stopped`, and both host and instance compute accounting now free its resources although the remote workload may still run.
4. A replacement create can use that budget.

Snapshot failure/timeout has the same assumption when changing pending_delete to stopped, even when a preceding stop was never confirmed. Retain compute independently of the UI lifecycle label, or derive the stopped transition only from a confirmed runtime stop. Exercise queued/unknown stop plus keep and snapshot-error regressions.

### Important 4: Accepting a reduced-limit agent redeploy forgets the previous actual commitment before runtime replacement succeeds

Location: `services/api/app/domain/agent_servers.py:1976–1985`, especially `cpus`/`memory_mb`; preservation logic at `agent_servers.py:1664–1670`.

`placement_candidate()` intentionally takes `max(desired limits, previous selection limits)` so lowering desired settings does not free an existing larger runtime's promise. But `deploy()` immediately overwrites those selection limits with the reduced desired settings when a redeploy is merely accepted. The old runtime can remain installed/running and the redeploy can fail before replacement; the original larger commitment has then been lost permanently.

Concrete trigger:
1. Running agent has a confirmed 8 GiB commitment.
2. Owner lowers desired limit to 2 GiB and requests redeploy.
3. Acceptance overwrites selection memory with 2 GiB.
4. SSH/upload/pull fails before the old container is replaced. `runtime_installed`/`installed_at` still describe the old installed runtime; `_deploy` only writes new installed evidence near successful completion (`agent_ssh_install.py:912–915`).
5. After the failed job releases host execution mutual exclusion, another admission sees only a 2 GiB commitment for an actual 8 GiB-limit runtime.

Keep prior actual/unknown promises separately from the desired attempt or retain their maximum until successful replacement is confirmed. Do not reduce the old commitment solely because a job was accepted or failed. Add a reduced-limit failed-redeploy regression.

### Important 5: Removing a host can cascade-delete reservations for an unknown create and orphan the remote workload

Location: `services/api/app/domain/runner_hosts.py:266–278`; unknown-create transition at `services/api/app/domain/runner_reclaim.py:153–155`; cascade at `services/api/app/models/runner.py:165–170` (RunnerEnvironment host FK) and command host FK.

Host removal still checks only creating/running/stopped/pending_delete. Unknown create results become `failed`, which the new quota/capacity contract deliberately retains until explicit confirmed cleanup. If those are the only retained workloads, an administrator can remove the host; its FK cascades delete the environment and command records despite the possibility that the remote create succeeded or later completes. Logical held count and reconciliation identity disappear without confirmed remote deletion; remote workload/volume can become unmanaged. Archived/deleted environments with retained snapshots also merit a preservation policy because host cascade removal deletes their environment records.

Concrete trigger: delay a create result until its command expires to unknown and environment becomes failed; remove the host while its runtime is unreachable. The live check passes although there has never been an authoritative remote deletion.

Block removal for all retained/unknown allocations, and preserve snapshot/archive records when retiring a node. Require explicit reconciliation/cleanup or a durable retirement record before dropping host identity.

## Checks and boundaries

- No Critical finding identified in the reviewed Task 1–4 code.
- Shared Placement schema rejects conflicting/extra targets; specified candidate filtering is hard-constrained; pure selector validates finite nonnegative telemetry and deterministic ordering.
- Agent independent probe uses fixed read-only script, bounded output/time, pinned password SSH channel, and trusted-only snapshots. The active implementation now invalidates snapshots on probe failure and contains a bounded worker sampler. No secret-handling finding identified.
- Count gate uses the instance row lock and counts all states except archived/deleted across hosts. Dedicated capability requests are persistent/deduplicated; sixth-call branch commits intentional approval output.
- Reviewed owner-role plus critical step-up before approval, separate requested_by/approved_by identities, immutable approval/request source comparison, grant revision/expiry checks, and stale-ack revoke protection. No concrete owner-identity or grant-activation bypass identified in current Docker-only enabled flow.
- Actual-host operations were changed to use environment host IDs; legacy singleton lookup rejects ambiguity. New placement respects local template/volume host affinity for creation and rejects specified/reuse mismatches.
- Source-only reproductions used standard-library stubs around actual extracted methods/pure functions. They do not claim database concurrency or real runtime validation. No database suites, full suites, real Kubernetes cluster, or network acceptance tests were run during this review because other agents were using the shared DB and Task 5 is out of scope.
