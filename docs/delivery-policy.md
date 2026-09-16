# Foundry delivery policy

The pipeline Foundry holds workers to, and which of its gates are mechanical
versus judgment. Derived from the operator's delivery and CI skills; the
skills stay the procedure and this document is the translation into what a
task must show and what a deterministic supervisor can check.

## Lifecycle stages (one pipeline, two marked divergences)

1. Write the change.
2. Lint with the same definition CI uses, from a repository-owned target.
3. Run it. Software: containers or a uniquely named throwaway local cluster,
   deleted even on failure, with the use case's success criteria confirmed
   and captured. Infrastructure: validate what can be validated and say
   what was not exercised.
4. One round of non-author adversarial review before the pull request
   opens. The reviewer gets the diff and the scope, not the author's
   reasoning.
5. Open the pull request because the work is done. Draft PRs are not an
   exemption. A red CI run is a defect in the local process and gets a
   one-line gap closure in the PR.
6. One round of external automated review, addressed in the same PR.
   Repository policy wins over this default.
7. Merge.
8. Release. Software: annotated `vMAJOR.MINOR.PATCH` tag on the default
   branch, pushed; the tag is the release. No version-bump PR. Infrastructure:
   ends at merge.

## Repository expectations

- Branching depends on one question: does anything else consume the default
  branch? Consumed: branch, PR, merge. Not consumed (notes, agent state,
  operator-only repositories): commit directly. Ambiguous: ask.
- Force-pushes and history rewrites need the operator's verbatim go.
- Workers operate in their own worktree or checkout at a detached start
  point, never in a primary checkout, and never share a working tree.
- Checked-in defaults track `latest`; the deployment repository holds the
  pin.
- CI placement is decided by capability, then cost. Pin actions to a commit
  SHA. Use per-ref concurrency groups. Release workflows trigger on the tag,
  refuse tags not reachable from the default branch, build, smoke, then
  publish.
- Publish is a one-way handoff. A repository's CI never deploys its product.
- Secrets never enter a repository, PR body, issue, or CI artifact.
- Public DNS records are never created by any automated flow.

## Required completion evidence

Result summary, changed files, branch and commits or PR, tests and checks
run with results, acceptance-criteria mapping, CI or release evidence, known
limitations, blockers, recommended follow-ups. Absence of any field fails the
report.

## Gate classification

### Deterministic: the supervisor enforces these

| Gate | Mechanical check |
|---|---|
| Report completeness | All required report fields present and non-empty |
| Scope containment | Diff touches only allowed paths; prohibited paths untouched |
| Worktree isolation | Worker ran in its own worktree, not a primary checkout |
| No injected identity committed | Shim and runtime files absent from the diff |
| Lint ran | Repository lint target executed, exit 0, log captured |
| Tests ran | Named test command executed, exit 0, log captured |
| Local run evidence exists | Named artifact (screenshot, log, endpoint transcript) present |
| Review round happened | A non-author review artifact exists for the reviewed SHA |
| Branch pushed at reviewed SHA | Remote branch head equals the reviewed SHA |
| PR exists and head matches | PR head SHA equals reviewed SHA |
| CI green | Workflow run for that SHA concluded success (the run, not the merge) |
| External review round | Reviewer bot signal present; absent means not yet |
| Release shape | Tag matches `vMAJOR.MINOR.PATCH`, annotated, reachable from the default branch |
| No secrets | Secret scanner over the diff, exit 0 |
| Timeout, retry, concurrency | Policy values applied, transitions recorded |
| Workspace clean | No leftover throwaway clusters or containers for the attempt |

### Judgment: Foundry or the operator

| Gate | Who |
|---|---|
| Whether the use case was actually seen working | Foundry, from the evidence |
| Whether review findings were genuinely addressed | Foundry |
| Whether "not exercised" in an infrastructure PR is acceptable | Foundry, escalate if material |
| Whether a red CI run's gap closure is real | Foundry |
| Branching mode when the repository is ambiguous | Operator |
| Whether a merge earns a release, and when to tag | Operator or the roadmap |
| Whether a tradeoff, limitation, or risk is acceptable | Operator |
| Whether scope grew and what becomes a follow-up | Foundry, escalate if it changes the outcome |
| Architectural soundness, user-experience acceptability | Foundry, escalate consequential calls |
