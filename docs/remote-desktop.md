# Remote desktop in Office

Commission: `/Users/slowember/Developer/Vaults/_meta/commissions/active/2026-10-02-aria-authorized-free-iphone-remote-desktop-in-ne.md`

## Outcome

Open **Desktops** in Office, choose your Mac, and sign in with that Mac's user
name and password. The free viewer runs in Safari, including iPhone Safari.
Tailscale must be connected when accessing Office away from the serving Mac.

## Failures checked before implementation

- Apple's server advertises RFB 003.889 and ARD security type 30. noVNC 1.7.0
  supports both; use its username/password prompt, never a stored VNC password.
- The Studio answers on loopback port 5900. The MacBook is online on Tailscale
  but refuses port 5900; report Screen Sharing unavailable rather than connected.
- A separate websockify network mount would bypass Office identity. Upgrade the
  WebSocket only inside the existing authenticated Office door, with exact Origin.
- Buffered HTTP input can consume the first WebSocket frame. Use unbuffered input
  for the Office handler and test a frame sent with the upgrade request.
- Upstream websockify lacks message limits. A subclass bounds raw input, queued
  frames and fragmented messages before decoding/delivery; cap concurrent sessions.
- noVNC needs data images, dynamic styles and an explicit same-host WebSocket CSP
  source. Scope these allowances to the desktop page.

## Implementation boundary

The serving Mac and online macOS peers owned by the same Tailscale user are
discovered; no manual machine list. Only discovered target IDs can connect, always
to port 5900. The bridge never accepts an arbitrary hostname or port from a client.
Office's Host and Tailscale login checks apply to every desktop page and API call.
The WebSocket additionally requires an exact same-host HTTP(S) Origin.

Mac credentials stay in the viewer's memory and go through Apple's ARD exchange;
Office neither stores nor logs them. No clipboard sync or external asset requests.
Closing/leaving the page disconnects. Mac Screen Sharing continues to enforce its
own allowed users. Enabling it also makes the native service available on the Mac's
LAN interfaces, independently of Office.

## References

- https://github.com/novnc/noVNC/tree/v1.7.0
- https://github.com/novnc/websockify/tree/v0.13.0
- https://support.apple.com/guide/mac-help/mh11848/mac

## Verification

- Real Studio RFB/ARD handshake reaches its username/password dialog in native
  Safari and an iPhone-sized Chrome viewport. A real logged-in framebuffer still
  needs the user to enter the Mac login directly in the viewer; no password was
  requested in chat or retrieved from the system.
- Browser QA: 375, 768 and 1440 px, no horizontal overflow or console errors.
  A controlled RFB server rendered a 960×600 framebuffer and recorded pointer
  clicks, `hello`, Enter and Command-A. Fit, Pan and Disconnect behaved correctly.
- `tests/test_desktops.py`: 12 passing tests, including auth/Origin refusal,
  same-owner discovery, session/message bounds, coalesced/fragmented WebSocket
  input and split JSON/signed webhook HTTP bodies.
- Existing targeted suites: phone 10, server 167, webhook 71, uploads 3 passed;
  JavaScript suite 17 passed. Scoped complexity gate passed with no added or
  regressed functions over budget.
- Full `npm test` reached one unrelated failure in
  `test_tower_review_holds_tbs_sensitive_merge_outside_kst_window`. The same
  failure was reproduced against an unmodified `git archive HEAD`; no desktop
  implementation is involved. The global complexity baseline also has unrelated
  drift; the scoped gate is clean. No baseline/checker was weakened.
- Evidence: `/Users/slowember/Library/Caches/nexus-office-desktop-qa/qa.json`,
  `input-events.json`, and chooser/desktop screenshots at all three sizes, plus
  `real-studio-chooser.png` and `real-studio-login.png`.
- Independent review: authenticated route and bridge reviewed in a separate
  context. Corrected Origin scheme matching, touch guidance and split-body reads.

## Current machine state

Studio: Screen Sharing available. MacBook: online and accessible over existing
SSH, but port 5900 refuses connections. Its account cannot enable Screen Sharing
without local administrator authentication; enable General → Sharing → Screen
Sharing on the MacBook. Office discovers it automatically once available.

Release receipts will be added after landing and deployment.
