# Security Policy

## Supported versions

Security fixes are applied to the latest published DitakNet monitoring release
(Docker/GHCR image tags such as `2.0.0`). Older tags may not receive backports
unless a support agreement says otherwise.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

1. Contact DitakNet support through the channels published on the product website
   or your deployment docs.
2. Use the subject prefix: `[SECURITY]`.
3. Include:
   - DitakNet version / image tag / commit
   - Steps to reproduce
   - Impact (auth bypass, data exposure, RCE, etc.)
   - Whether you have a proof-of-concept (describe; do not attach malware)

We will acknowledge receipt when possible and coordinate a fix before public
disclosure.

## Scope

In scope examples:

- Authentication / session handling on the monitoring UI and API
- Privilege escalation between roles
- Secret leakage via logs, responses, or committed files
- Injection / XSS / CSRF in application forms and APIs

Out of scope examples:

- Scanning or attacking networks you do not own or administer
- Issues that require private customer databases or activation materials
- Social engineering against operators

## Secrets and private materials

Never commit:

- Real `.env` / `*.local.env` files
- Activation codes, private signing keys, PEM private keys
- Customer databases (`data/`, `*.db`, `*.sqlite3`)
- Payment receipts or `DOCUMENTS_OUTSIDE_SERVER/` materials
- Live `logs/` and `backups/` contents

If a secret is committed by mistake, rotate it immediately and report with
`[SECURITY]` so history cleanup can be coordinated.
