# Security

Foundry runs inside an AI coding harness with whatever permissions that
harness grants. It holds no credentials of its own. Its state directory can
contain task contracts and worker reports that describe private work;
protect it with filesystem permissions and keep it out of every repository.

Report a vulnerability privately through GitHub's security advisory form for
this repository, not as a public issue. Expect an acknowledgement within
seven days.

Never commit to this repository: credentials, harness authentication state,
transcripts, task payloads, or details of a specific deployment.
