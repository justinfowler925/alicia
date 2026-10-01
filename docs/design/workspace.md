# Alicia workspace

Source: Shine flowbite-dashboard page structure (persistent navigation, primary feature, summaries and activity). Product precedent: Alicia session.html and its native controls and theme tokens; no second component framework. Hollywood generated the observatory artwork on Studio; job and asset provenance are in static/workspace-artwork.json.

Routes: Overview, Alicia, Actions, Projects, Forge, Scout, Demo Maker, Clearspeed Demos and Hollywood. Navigation stays available, with a disclosure menu below 760px. Existing Alicia voice session DOM is retained across routes. Actions read and update the existing durable todo store; pagination is 20 entries. Scout reads bounded feed receipts and the registered Studio observer, never starts jobs. Unknown and stale evidence is displayed explicitly. Logs retain the observer's redaction and allowlist.

Acceptance: actual action counts, working search and stage updates, real Scout jobs with logs and observed timestamps, Forge accessible through authenticated Studio HTTPS, usable application links, no horizontal overflow at mobile width, keyboard focus visible. Browser verification uses Codex browser controls; no claim is made that Shine's separate Playwright aggregate verifier was run.
