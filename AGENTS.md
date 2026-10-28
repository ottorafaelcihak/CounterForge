# Project workflow

## MVP Git workflow

- Make MVP changes and commits directly on `master`.
- Do not create feature branches or separate worktrees for MVP work unless the user explicitly requests them.

## Three-provider development workflow

For substantive implementation, fixes, and refactors, use the installed
`develop-with-agent-team` skill with the agent-team bridge and these three
providers: Codex, Claude Code, and Muse. The current chat coordinates scope,
integration, and reporting. Native Codex subagents alone do not satisfy this
workflow. Scale assignments to the task; a small documentation edit does not
require three duplicate reviews.

### Default roles

| Provider | Responsibility |
| --- | --- |
| Codex | Implement a bounded change and run the relevant checks. |
| Claude Code | Independently review correctness, edge cases, and the completed diff. |
| Muse | Check acceptance criteria, integration behavior, and verification coverage. |

Rotate roles when useful, but give each available provider a meaningful, focused
assignment. Avoid assigning the entire implementation independently to all three.

### Coordination rules

1. Read repository instructions, Git status, the applicable `docs/plans/plan-XX.md`,
   and its referenced specifications. Define acceptance criteria and file ownership
   before delegating.
2. Discover the bridge tools and inspect `team_health` and `team_models` when
   available. Use supported bridge or CLI model defaults; desktop model names may
   not be valid CLI models. If a stale MCP connection disagrees with fresh
   `agent-team call team_health` output, use the fresh CLI and report that the
   connection needs reloading. Pass CLI arguments safely without interpolating
   worker prompts into shell commands.
3. Give each worker an absolute workspace path, objective, acceptance criteria,
   relevant files, allowed edits and checks, and a request to return changed files,
   exact check results, and blockers. Use read mode for exploration and review;
   enable edit mode only for the assigned implementation. Workers check their
   inbox at task boundaries and before finishing. Do not delegate recursively
   unless the coordinator explicitly assigns that responsibility.
4. Keep **one editor per workspace**. During MVP work, serialize editing workers
   in the current `master` checkout and run independent read-only work in parallel.
   The coordinator stays read-only while a worker owns editing. Preserve existing
   changes; do not reset, stash, overwrite, or commit them merely to ease delegation.
5. Track worker IDs, providers, models or configured defaults, assignments,
   workspaces, and status. Check `team_status` at reasonable intervals and read the
   full output file when the returned tail is insufficient. Silence alone does
   not establish a hang: inspect elapsed time, deadlines, tool activity, and
   supervisor liveness. Before reassigning an editing task, confirm the previous
   worker has a terminal status **and `supervisor_alive: false`**. For older bridge
   versions, verify termination through the fresh CLI; a cached cancelled status
   is insufficient. Follow-up work for a finished worker requires a new job.
6. Treat installation and authentication as separate checks. If a model fails,
   inspect the fresh bridge configuration and retry once with a supported default.
   Report any model switch. If authentication is expired or access is denied,
   report the blocked provider and continue useful work with available providers;
   for expired Claude authentication, identify `claude auth login` as the recovery
   step. Never claim three-provider participation when a provider did not run. If
   the user explicitly requires all three, keep that requirement outstanding.
7. Inspect the actual completed diff and have a different available provider
   review it. Resolve material findings, then run appropriate checks against the
   final workspace. Worker completion claims are not verification evidence.
   Report what changed, which providers and models actually participated, check
   results, and any remaining limitations.

Delegation does not expand the user's authorized scope or grant permission to
publish, message others, or access credentials.
