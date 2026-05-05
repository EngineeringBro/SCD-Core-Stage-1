# SCD Core Stage 1 Blueprint

## Purpose

This document defines the simplest Stage 1 behavior for SCD Core.

The system runs in a strict sequence. If one step fails, the orchestrator must terminate and not skip ahead.

## Flow

1. Trigger
   GitHub Actions cron runs on schedule.

2. Orchestrator
   The cron trigger starts `orchestrator.py`.
   The orchestrator runs every required step in order.
   If a required step fails or does not return the expected output, the orchestrator terminates.

3. Fetcher
   `jira_read.py` pulls full Jira ticket details.
   Required fetch scope:
   - fields
   - comments
   - organization
   - any other ticket context needed for routing

   Environment behavior:
   - `SCAN_TICKET_ID` enables single-ticket mode.
   - When `SCAN_TICKET_ID` is set, the fetcher pulls only that ticket.

4. Router
   `router.py` sends the full ticket details to GPT-4o-mini.
   The router must return:
   - the ticket id
   - the best matching module name

   Routing rule example:
   - if the ticket details describe an orphaned transaction request, the router returns the orphaned transaction module

5. Module Step
   After routing, the orchestrator runs the matched module.

   Initial Stage 1 modules:
   - notification module
   - spam module
   - general module
   - orphaned module

   Stage 1 module behavior:
   - each module checks whether the ticket id matches a hardcoded allowed id list
   - leave space in each script for manual id entry
   - if the id matches, output `yes`
   - if the id does not match, output `no`

6. GitHub Issue Output
   The final module output must appear in a GitHub issue description.
   Expected output shape for Stage 1:
   - ticket id
   - selected module
   - module result: `yes` or `no`

## Required Execution Contract

The orchestrator must enforce this exact order:

1. trigger
2. fetcher
3. router
4. module
5. GitHub issue description output

If any step does not complete successfully, the workflow stops.

## Minimal File Plan

The first implementation should be built around these files:

- `orchestrator.py`
- `jira_read.py`
- `router.py`
- `modules/notifications_module/notification_module.py`
- `spam_module.py`
- `modules/general_module.py`
- `modules/orphaned_module.py`

## Per-Module Placeholder Rule

Each module should contain an editable section for manual ticket id input.

Example behavior target:

- notification module: if ticket id matches configured id, output `yes`
- spam module: if ticket id matches configured id, output `yes`
- general module: if ticket id matches configured id, output `yes`
- orphaned module: if ticket id matches configured id, output `yes`

If no configured id matches, the module outputs `no`.

## Stage 1 Goal

Stage 1 is not full automation.

Stage 1 proves that:

- GitHub Actions can trigger the pipeline
- the orchestrator can enforce sequence
- Jira ticket data can be fetched
- routing can pick a module name
- the matched module can return a simple `yes` or `no`
- the result can be written into a GitHub issue description