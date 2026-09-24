# Explicit task delivery profiles

Repositories opt in through `.codex/delivery.yaml` with `schema_version: 2`.
The caller must name a profile; task titles and prose never select one. Schema1
remains supported with its original raw-byte SHA-256 digest and old receipts.
Passing `--profile` to a schema1 policy is an error, including an empty value.

This illustrative Brain policy separates launcher changes from Salesforce work;
it does not edit or replace any actual Brain policy:

```yaml
schema_version: 2
repository: github.com/example/fowler-brain
requirements:
  - id: tests
    kind: test
    command: ./scripts/test.sh
    freshness_hours: 4
  - id: git
    kind: git
    probe: Delivered commit is reviewed and present on the intended branch
    freshness_hours: 4
  - id: owner-review
    kind: other
    probe: Owner reviews the delivered artifact
profiles:
  launcher:
    requirements:
      - id: launcher-smoke
        kind: test
        probe: Launcher starts with the intended configuration
  salesforce:
    requirements:
      - id: sandbox-validation
        kind: test
        probe: Salesforce sandbox validation succeeds through the approved pipeline
        target: partial
      - id: sandbox-readback
        kind: readback
        probe: Affected principal can use the changed component in the sandbox
        target: partial
      - id: sandbox-rollback
        kind: rollback
        probe: Reviewed rollback artifact is available for the sandbox change
        target: partial
```

Common requirements are concatenated with the selected profile's requirements.
They cannot be overridden or removed by a profile. Every effective union must
contain at least one **required** `test` and one **required** `git` requirement.
Duplicate ids anywhere in a union fail, even if the duplicate says
`required: false`. Empty common/profile lists are allowed when the union meets
these rules. Unknown fields, malformed profiles (even unselected ones), duplicate
YAML mapping keys, and missing/unknown selections fail closed. Profile names use
lowercase letters, digits, `_` and `-`, starting with a letter or digit.

## Bind and capture

Use the existing work item in the intended store. These commands illustrate an
owner-authorized local CLI session; policy selection grants no execution,
Salesforce, deployment, approval, acceptance, or closure authority.

```sh
.venv/bin/python -m alicia workflow --db /path/to/canon.sqlite \
  policy /path/to/repository --profile launcher --bind WORK_ITEM_ID

# For Salesforce tasks, explicitly bind --profile salesforce instead.
.venv/bin/python -m alicia workflow --db /path/to/canon.sqlite \
  receipt WORK_ITEM_ID --requirement-id tests --type run_output \
  --content-ref /path/to/test-output.log --result pass \
  --artifact-digest COMMIT_SHA --captured-by codex --captured-by-kind worker

.venv/bin/python -m alicia workflow --db /path/to/canon.sqlite \
  delivery WORK_ITEM_ID
```

The Python equivalent is `load_delivery_policy(repo, profile="launcher")`,
followed by `bind_delivery_policy(work, loaded)` and `store.save(work)`.
`loaded.policy.requirements` contains the effective union. The CLI prints the
selected profile and digest. `WorkItem.delivery_policy_profile` stores the name;
old records default to `""`. Existing JSON-backed Canon storage needs no DDL or
backfill. Binding preserves lifecycle state and approval/decision references.

Schema2 hashes `b"alicia-delivery-policy-v2\0" + profile.encode("utf-8") + b"\0"`
followed by the raw YAML bytes. Different profiles have different digests even
when their effective requirements are identical. Any policy-byte change also
changes the digest. Binding snapshots the policy; editing the file alone does
not rebind existing work. Rebind explicitly and collect receipts for the new
binding.

Receipt attachment stamps `metadata.delivery_policy_digest` from the stored
work item and records its source repository. For profile-bound work, evaluation
requires that digest, the correct linked work item, and no conflicting source
repository. It also enforces existing freshness, artifact, target, verified,
and passing-result checks. Receipts without digest metadata remain compatible
only with schema1/unprofiled work. Rebinding between profiles cannot reuse old
receipts just because requirement ids overlap.

`--captured-by` defaults to `owner-local-verifier`; `--captured-by-kind` defaults
to `human`. The corresponding optional Python arguments preserve old callers.
Actors must be nonblank and kinds must be `human` or `worker`. A Codex capture
should explicitly use `--captured-by codex --captured-by-kind worker`.

Capture identity describes provenance, **not reviewer authority**. The existing
owner-facing receipt command still obtains the configured owner's principal and
persists verification through Canon's identity-checked store. It does not prove
an independent human review happened. A worker principal cannot verify evidence;
an automated verifier cannot replace the owner in this attachment operation.
Canon's separately allowlisted automated-verifier store path remains unchanged.
Independent review, approval and acceptance gates remain the operator's job.


## Repository profiles and scoped installation

Alicia selects `application` for a full application release: every prior v1
requirement, including the full pytest suite and application SHA readback, remains.
Select `workflow_control` only for policy/receipt/Canon workflow changes. It requires
workflow, HTTP, identity/state and efficiency tests, lint, Git, an installed module
hash manifest with unrelated-file preservation, and live bound-profile readback.
It does not certify the unrelated voice/application surface or replace its gates.
The full suite is still measured against baseline and failures recorded separately.
Profiled receipts dated in the future fail freshness; schema1 behavior is unchanged.

Codex integration preserves the original Forge commit/run and adds the repository
profile migration plus future-date rejection. The original run remains blocked.
