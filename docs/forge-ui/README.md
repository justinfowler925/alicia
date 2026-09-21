# Forge conversation cleanup

Forge now uses one header, compact conversation selection, a centered readable
transcript and an integrated composer. Progress is visible in one summary; timing,
turn counts and model provenance expand under Details. Error and reconnect messages
remain visible. Existing history, file transport, cancellation and execution APIs
are unchanged. The composer remains visible across desktop and mobile widths.

## Verification

- `scripts/verify-forge-ui.cjs`: eight browser scenarios, including send/reply,
  history, keyboard input, upload retry/removal and connection recovery. API fixtures
  are isolated in the browser; this is frontend workflow proof, not inference proof.
- 42 Forge Python tests passed. Workflow lint gate and JavaScript syntax passed.
- Full suite: 981 passed, 31 failed. All 31 failures reproduce on the unmodified
  installed baseline `3d3b57684bca6125608e6cb3178d12918eed985a`.
- Shine measure: passed, zero axe violations. Layout: 20 scenarios passed across
  390/768/1280/1440/1920 widths with long content and enlarged text.
- Both light and dark themes inspected at 390/768/1440/1920 widths.
- Shine usability contract passed for activity disclosure and multiline composition.

## Proof limitations

The selected `spectrum-ai-chat` source describes conversation components, but its
catalog screenshot depicts the Spectrum marketing site. It does not establish chat
visual equivalence. The Brutus sibling retains voice-specific panels absent from
Forge, so strict whole-page product comparison cannot pass. Shared tokens, native
controls, workspace navigation, turns and attachment handling are preserved; the
single-column chat layout is an intentional divergence. No overall Shine completion
certificate is claimed. The UI-only installation is separately hash-verified; backend
version and service deployment status are not relabeled as a full release.

Studio knowledge retrieval over the old Tailscale address was unavailable during
discovery. Current installed source and the live local Forge endpoint were inspected.
Canon work item: `c0b832ba-7440-4096-bec5-9e340aebe630`.
