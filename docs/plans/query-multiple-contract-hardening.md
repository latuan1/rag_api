# Query Multiple Contract Hardening

Status: Implemented. The behavior described here is covered by existing model
validation, route, and pgvector filter tests.

## Goal

Align `POST /query_multiple` with the Personal Knowledge API contract while preserving the existing route and tuple-array response shape.

## Changes

- Validate `QueryMultipleBody` with trimmed non-empty `query`, 1-50 deduped non-empty `file_ids`, `k` bounded to `1..50`, and optional trimmed non-empty `entity_id`.
- Build one vector-store filter containing both requested file IDs and authorized ownership scopes.
- Keep one cached query embedding call and one vector search for global ranking.
- Return `200 []` for empty authorized results instead of `404`.
- Leave `/query`, `/embed`, context, and delete contracts unchanged.

## Tests

- Model validation covers trimming, dedupe, empty values, `k` bounds, and invalid `entity_id`.
- Route tests verify file/user filter construction, entity scope inclusion, duplicate ID normalization, empty-result behavior, and tuple response shape.
- Pgvector integration guardrails verify the combined file/user metadata filter avoids `jsonb_path_match`.

## Acceptance

- Authorized file IDs return globally ranked tuples.
- Mixed or fully unauthorized/unknown file sets do not disclose existence and may return partial results or `[]`.
- Invalid batch request payloads return `422`.
