# ADR-001: Hosting on a sleeping MacBook (2026-09-23)

**Decision.** The backend runs on the user's MacBook via launchd (KeepAlive). The machine sleeps when closed.

**Consequences.**
- A wake detector (`maintenance.catch_up`, every 60 s) notices event-loop gaps > 20 min and immediately refreshes stale series and re-evaluates alerts.
- All jobs use `coalesce=True` and a 1-hour `misfire_grace_time`; the morning brief (v1) is sent at first wake after 06:30 IL if not yet sent.
- Real-time alerts and quotes are **best effort** while asleep; SYS shows last-run/stale state.
- Israeli sources are geofenced (CBS, TASE, Globes/Calcalist RSS, boi.org.il pages); adapters carry `egress="il"` so a future VPS can route them through a Tailscale exit node on the Mac.
- Optional: `caffeinate -s` during market hours (user-controlled; `pmset` needs sudo).
