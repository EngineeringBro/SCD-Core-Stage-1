# Knowledge

This folder stores local knowledge artifacts used to train routing and module behavior.

## Layout

- `raw/`
  Compressed historical ticket caches. These are the source inputs for pattern mining.

- `mined/`
  Derived summaries and aggregate pattern outputs generated from the raw caches.

- `learned/`
  Hand-curated corrections, overrides, and ticket-specific guidance captured from review.

- `portal_refs/`
  References to the Jira customer portal gathered from ticket history, plus the canonical portal link.

- `knowledgebase/`
  Local API-backed mirror of the Jira Service Management portal structure plus linked Confluence knowledge-base pages.

## Current files

- `raw/tickets_cache.jsonl.gz`
  Combined cache snapshot.

- `raw/tickets_cache_2020.jsonl.gz` through `raw/tickets_cache_2026.jsonl.gz`
  Year-partitioned historical ticket caches.

- `mined/mined_patterns.json`
  Aggregate mined statistics and confidence summaries built from the raw caches.

- `learned/unknown.yaml`
  Manual guidance and module overrides for tickets that needed correction.

- `portal_refs/README.md`
  Explains what portal-related material is stored locally and what is not.

- `portal_refs/portal_link.txt`
  Canonical Jira customer portal URL.

- `portal_refs/ticket_portal_references.json`
  Ticket-derived references to the customer portal or knowledge-base content extracted from raw caches.

- `knowledgebase/manifest.json`
  Export summary for the latest API-backed mirror run.

- `knowledgebase/service_desks/`
  Per-desk service desk metadata, request type groups, request types, and request type fields.

- `knowledgebase/spaces/`
  Per-space Confluence page exports, including raw JSON and `body.export_view.html` files.

## Usage

Read in this order:

1. `README.md` for structure
2. `learned/` for explicit overrides
3. `knowledgebase/` for the current local copy of portal structure and knowledge-base content
4. `portal_refs/` for ticket-derived portal references and the canonical link
5. `mined/` for historical patterns
6. `raw/` only when you need to re-mine or inspect original ticket history
