# Host Command Center v0.4 — Live Shadow Mode

V0.4 moves the project out of demo-only behavior and into **real-provider Shadow Mode**.

## What is live now

- Gmail OAuth is real. Test and approval emails are sent through the Gmail API.
- Hospitable property sync is real and read-only.
- Hospitable reservation reconciliation is real and read-only.
- Pending reservation requests can create real approval cards and one deduplicated host email.
- Accepted/cancelled/not-accepted status changes are reconciled from Hospitable as the source of truth.
- Guest messages can be read from Hospitable into the app; sending remains disabled.
- The app periodically reconciles provider state and can also use webhook files as a trigger for immediate reconciliation.

## What is intentionally NOT live yet

- Accepting or declining an Airbnb/Hospitable reservation.
- Sending guest messages.
- Changing Airbnb/Hospitable pricing or availability.
- Creating remote cleaning/maintenance tasks.

Those pages/actions are visibly marked as unavailable rather than silently simulating success.

## Safety model

The app is now **live-read + live-notify**, but still **no live writes** to Airbnb/Hospitable. This is intentional. We first observe real provider data and compare Host Command Center decisions against manual actions.

## Setup

1. Extract the folder somewhere permanent.
2. Run `setup.bat` once.
3. Open the desktop shortcut.
4. Settings -> Run Setup Wizard.
5. Complete Gmail OAuth and send the **LIVE** test email.
6. Add/test the read-only Hospitable PAT.
7. Sync live properties + reservations.
8. Review Health and run the robustness lab.

If v0.3/v0.2/v0.1 sits beside this folder, v0.4 can import the prior database and Windows-DPAPI encrypted secrets on first launch.

## Validation

- Developer tests: 23/23 passed.
- Robustness lab: 10/10 passed.
- Self-test: PASS.

The robustness lab covers concurrent duplicate events, malformed input, expired requests, double-clicks/replays, terminal-state resurrection, missing-data fail-safe behavior, duplicate approval-email prevention, provider source-of-truth reconciliation, and restart persistence.
