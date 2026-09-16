# Example state directory

Copy this layout to `$FOUNDRY_STATE_DIR` (default `~/.local/state/foundry`)
and replace the contents with your own. Nothing in the real directory should
ever be committed to a repository.

```
operator-rules.md        your standing rules; see the example
bootstrap-contract.md    what you asked Foundry to do, in your words
ledger.sqlite            created by `foundry-ledger init`
bootstrap-evidence/      read-only exports and evidence
contracts/               task contracts issued to workers
```
