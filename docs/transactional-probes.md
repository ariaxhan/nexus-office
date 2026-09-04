# Transactional probe delivery map

Issue #94 is split into six bounded deliveries:

- #95: isolated sandbox schedule, cadence bounds, core isolation, retained evidence
- #96: no-charge checkout creation, redirect verification, cleanup
- #97: care consent, session, deletion, and rate-limit lifecycle
- #98: Kakao and SMS send caps and receipt mapping
- #99: PWA playback, offline shell, and installability
- #100: shared KR, EN, and ES journey with locale-specific evidence

All six follow live repair closure in #93. Product probes follow the scheduler in #95. Each product probe owns sandbox cleanup and redacted failure evidence.
