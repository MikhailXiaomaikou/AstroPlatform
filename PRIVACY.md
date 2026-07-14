# Privacy Notice for the Reference Implementation

This document describes what the current Standard Astro code can process. It
is not a substitute for the privacy notice of a particular hosted instance.
Anyone operating a deployment is responsible for publishing accurate contact,
retention, jurisdiction, subprocessors, and user-rights information for that
deployment.

## Data the application can process

Depending on the features a user chooses, the application can store or process:

- account data, including username, email, password hash, display name, avatar,
  OAuth identifier, subscription tier, and account timestamps;
- saved chat messages, titles, agent/tool audit trails, workspace and research
  records, schedules, comments, paper drafts, uploaded files, generated
  artifacts, and provenance/evidence records;
- user-supplied model-provider API keys. Newly saved or updated keys use a
  Fernet-encrypted database field and the application does not return the
  secret value after saving it. Upgrades from older versions can still contain
  legacy plaintext JSON or a legacy Anthropic-key column; operators must audit
  and migrate those rows;
- service metadata such as provider/model choice, token counts, estimated
  cost, latency, task status, errors, page/event type, session identifiers, and
  timestamps;
- product-event metadata. For a sent chat message the current frontend records
  character and word counts plus up to eight tokenized topic keywords, so this
  metadata can contain short fragments of the user's text. Tool events can
  also contain truncated JSON parameters and error text. The event collector
  does not currently apply a field-level content scrubber;
- network identifiers such as client IP addresses used for rate limiting,
  abuse controls, comment audit records, and infrastructure logs.

Content intentionally published or shared by a user, such as a public paper,
shared session, or public comment, can be visible to other people. Do not put
secrets or confidential research data in public content.

## How data is used

The reference implementation uses these data to authenticate users, provide
and resume research workflows, call the selected model and archive services,
enforce quotas and authorization, diagnose failures, validate scientific
claims, preserve provenance, and understand aggregate feature usage.

The repository contains analytics records that are described in code as future
training signals, but it does not itself implement an automatic export of user
content into a model-training pipeline. A deployment operator and each selected
third-party provider may have separate policies; those policies must be
reviewed before sending sensitive material.

## External services

Requests can leave the deployment when a user or operator enables an external
AI provider, Google sign-in, astronomy archive or literature service,
S3-compatible object storage, hosting/logging provider, or another configured
integration. The relevant service receives the data needed to perform that
request. In particular, chat content and tool context are sent to the selected
model backend.

On a trusted local machine, the optional Codex or Claude CLI bridge passes the
assembled prompt to the locally installed subscription CLI. The associated
model-provider terms still apply. Those bridges and the Bot Console are disabled
for hosted production.

## Browser and local-machine data

The frontend uses browser storage for authentication and user-scoped interface,
chat, workspace, and operation-log state. The local operation log can include a
complete chat query. Anyone with access to the same browser profile may be able
to access that state. Signing out removes the authentication token but does not
clear chat, workspace, or operation-log data; clear the site's browser data
before handing a shared device to another person.

## Retention and deletion

The code does not yet impose one universal retention period. Database backups,
object-store versions, platform logs, and recovery bundles may outlive deletion
from the live application according to the deployment operator's policy.

Authenticated users have targeted endpoints for individual saved chat
sessions, stored API keys, uploaded FITS files, saved objects, research memory,
schedules, session comments, and several other user-owned records. These are
not account-level erasure: related paper, share, snapshot, embedding,
provenance, research-job, log, or backup records may remain, and storage or
foreign-key failures can prevent complete deletion. Public comments are
soft-deleted by an operator, so their stored content and audit IP can remain.
Unpublishing removes public access to a paper but is not the same as deleting
every underlying record. The current API has no self-service endpoint that
deletes an entire account and all related data; users must contact the operator
of their deployment for that request.

## Security and accuracy

Passwords are stored as hashes and current BYOK credentials are encrypted at
rest, but no system can guarantee absolute security. Operators must use TLS,
stable externally managed encryption/signing keys, access-controlled backups,
and the production settings documented in `DEPLOYMENT.md`.

AI-generated output can be wrong. Standard Astro's evidence gates reduce this
risk but do not make the service a source of medical, legal, financial, or
other professional advice.

## Questions and changes

For a hosted instance, contact its operator using the contact information that
operator publishes. For questions about this reference implementation, open a
repository issue without including personal data or secrets. Material changes
to the implementation should update this file in the same pull request.
