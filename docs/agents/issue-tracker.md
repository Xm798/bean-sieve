# Issue tracker: Linear

Issues live in Linear, team Apps (`APP-123`), project `Bean-Sieve`, via the `linear` CLI. Every issue belongs to the `Bean-Sieve` project.

**PRs as a request surface: no.**

## Commands

- **Create** ("publish to the issue tracker"): `linear issue create --project Bean-Sieve --title "..." --description-file body.md --label <label> --yes`; add `--parent APP-N` for a sub-issue.
- **Read** ("fetch the relevant ticket"): `linear issue view APP-N --no-pager`; `--json` adds labels, state, parent and children.
- **List**: `linear issue query --project Bean-Sieve --json`, filtered with `--label` / `--state`.
- **Comment**: `linear issue comment add APP-N --body "..."`.
- **Labels / state**: `linear issue update APP-N --add-label ... / --remove-label ... / --state "..."`.
- **Close**: comment the outcome, then `--state Done` (or `Canceled`).

## Wayfinding operations

- **Map**: an issue labelled `wayfinder:map`; its description holds Notes / Decisions-so-far / Fog.
- **Child ticket**: a sub-issue of the map, labelled `wayfinder:<research|prototype|grilling|task>`.
- **Blocking**: `linear issue relation add <blocker> blocks <child>`; unblocked once every blocker is Done or Canceled.
- **Frontier**: the map's `.children` (`linear issue view <map> --json`) in backlog/unstarted state, unassigned and unblocked; first listed wins.
- **Claim**: `linear issue update APP-N --assignee @me --state "In Progress"`.
- **Resolve**: comment the answer, `--state Done`, then add a gist + link to the map's Decisions-so-far.
