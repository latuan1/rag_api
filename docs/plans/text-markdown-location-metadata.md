# Text and Markdown Location Metadata

Status: Implemented. The behavior described here is covered by existing
document-location unit tests and `/embed` route tests.

## Goal

Store stable `start_line`, `end_line`, and optional Markdown `section` metadata on chunks created from supported text-like uploads without changing the `/embed` multipart contract.

## Changes

- Add a document-location helper for supported text extensions: `txt`, `md`, `markdown`, `json`, `log`, and `csv`.
- Annotate loaded documents by mapping loader output back to source file line ranges.
- Derive Markdown section metadata from the nearest preceding heading.
- Split documents per source document so each stored chunk receives a narrower line range when source line metadata is available.
- Keep unsupported formats, including PDFs, on their existing loader-provided metadata such as `page`.

## Tests

- Unit tests cover text line mapping, chunk-level line narrowing, Markdown section detection, unsupported file behavior, and mapping failures.
- Route tests verify `/embed` stores `file_id`, `user_id`, `digest`, `start_line`, and `end_line` for text uploads.

## Acceptance

- Plain-text and Markdown chunks include stable source line metadata when mapping succeeds.
- Markdown chunks include `section` when a heading is discoverable.
- Mapping failures do not fail ingestion.
- Existing `/text`, PDF, and non-text ingestion behavior remains compatible.
