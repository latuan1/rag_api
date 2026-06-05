# Chunking Strategy Improvement Spec for `rag_api`

## 1. Summary

This spec proposes an upstream-friendly improvement to the chunking strategy in [`danny-avila/rag_api`](https://github.com/danny-avila/rag_api).

The current implementation uses a generic recursive character splitter for all supported document types. This proposal introduces a balanced, structure-aware default while preserving the existing recursive strategy as a legacy fallback.

The goal is to improve retrieval quality and citation metadata without significantly increasing embedding cost or changing LibreChat's existing RAG API contract. The MVP also adopts a lightweight, deterministic subset of contextual retrieval by prepending compact metadata-derived context to chunk text when useful.

## 2. Accepted Decisions

- Target contribution model: upstream PR to `danny-avila/rag_api`.
- Proposed new runtime default: `balanced` preset.
- Balanced strategy: `auto` / block-aware chunking.
- Legacy behavior remains available through `recursive`.
- MVP file coverage: all file types currently supported by the original `rag_api` loader path.
- PDF chunks may span multiple adjacent pages when useful, with a maximum span of 2 pages.
- Markdown and DOCX heading paths should be added to chunk text through deterministic contextual prefixes.
- Contextual prefixes must count toward final chunk size.
- The MVP uses deterministic contextual prefixes derived from loader metadata. It does **not** call an LLM to generate per-chunk context.
- If `auto` cannot preserve structure, it must fallback to recursive chunking, log a warning, and set `chunk_strategy = "recursive_fallback"`.
- Metadata changes are additive-only. Existing keys such as `file_id`, `user_id`, and `digest` must not be renamed or removed.
- Parent-child retrieval is out of MVP and deferred to future work.
- `/chunk/preview` is out of MVP.
- Re-index endpoint is out of MVP.
- Implementation should be split into 3 incremental PRs.

## 3. Current Behavior

Current ingestion flow:

```text
file upload
  -> get_loader()
  -> loader.lazy_load()
  -> List[Document]
  -> RecursiveCharacterTextSplitter.split_documents()
  -> add metadata: file_id, user_id, digest
  -> embed chunks
  -> store in vector DB
```

Current defaults:

```env
CHUNK_SIZE=1500
CHUNK_OVERLAP=100
```

Current limitations:

- Same chunking strategy is used for all document types.
- Paragraphs, headings, tables, code blocks, pages, slides, sheets, and rows are not treated as first-class boundaries.
- Citation metadata is not normalized across file types.
- Chunking logic is coupled to ingestion route code.
- Chunk text can lose important document context after splitting.

## 4. Goals

The improved chunking system should:

1. Preserve the existing API contract used by LibreChat.
2. Improve retrieval quality with structure-aware chunk boundaries.
3. Improve chunk-level context using deterministic metadata-derived prefixes.
4. Avoid a large increase in embedding cost.
5. Keep recursive splitting available as a stable fallback.
6. Add normalized metadata for better citation and debugging.
7. Move chunking logic out of route handlers into a dedicated service/module.
8. Support all file types currently handled by `rag_api` loaders.
9. Degrade gracefully when document loaders do not expose enough structure.

## 5. Non-goals

The MVP does not include:

- Parent-child retrieval.
- Hybrid search or reranking.
- BM25 / lexical indexing.
- LLM-generated contextualization per chunk.
- Token-based chunk sizing.
- OCR for scanned PDFs.
- Audio/video transcription.
- New LibreChat UI changes.
- New `/chunk/preview` endpoint.
- New re-index endpoint.
- Vector DB schema redesign.

## 6. Sizing Semantics

All chunk sizing variables are measured in **characters**, matching the current behavior of LangChain's `RecursiveCharacterTextSplitter`:

```env
CHUNK_SIZE
CHUNK_OVERLAP
MIN_CHUNK_SIZE
MAX_CHUNK_SIZE
MAX_CONTEXTUAL_PREFIX_LENGTH
```

Token-based splitting is intentionally out of scope for this MVP. A future version may add tokenizer-aware chunking, but it should not be required for this PR series.

When structure-aware chunking prepends metadata-derived text, such as a contextual prefix, that prefix must be included in the final chunk length calculation.

Example:

```text
[Context: file=README.md; section=Installation > Docker]

Actual chunk content...
```

The chunker should reserve space for the prefix before adding body content, so the final chunk text stays within `MAX_CHUNK_SIZE` except when an atomic block is already larger than the limit.

When `CONTEXTUAL_PREFIX_ENABLED=true`, `CHUNK_OVERLAP` should apply only to the body content, not the contextual prefix. The chunker should first select the overlapped body text, then generate and prepend the contextual prefix for that final chunk. This keeps overlap useful for content continuity and avoids spending overlap budget on repeated prefix text.

## 7. Proposed Configuration

Keep existing env variables:

```env
CHUNK_SIZE=1500
CHUNK_OVERLAP=100
```

Add:

```env
CHUNKING_PRESET=balanced
CHUNKING_STRATEGY=auto
MIN_CHUNK_SIZE=300
MAX_CHUNK_SIZE=2200
PRESERVE_STRUCTURE=true
CONTEXTUAL_PREFIX_ENABLED=true
MAX_CONTEXTUAL_PREFIX_LENGTH=240
MAX_HEADING_PATH_DEPTH=4
MAX_METADATA_STRING_LENGTH=512
```

Recommended balanced runtime defaults:

```env
CHUNKING_PRESET=balanced
CHUNKING_STRATEGY=auto
CHUNK_SIZE=1200
CHUNK_OVERLAP=120
MIN_CHUNK_SIZE=300
MAX_CHUNK_SIZE=2200
PRESERVE_STRUCTURE=true
CONTEXTUAL_PREFIX_ENABLED=true
MAX_CONTEXTUAL_PREFIX_LENGTH=240
MAX_HEADING_PATH_DEPTH=4
MAX_METADATA_STRING_LENGTH=512
```

Legacy fallback config:

```env
CHUNKING_PRESET=legacy
CHUNKING_STRATEGY=recursive
CHUNK_SIZE=1500
CHUNK_OVERLAP=100
CONTEXTUAL_PREFIX_ENABLED=false
```

Default resolution should be explicit so the new runtime default is intentional and rollback-friendly:

| Scenario | Effective preset / strategy | Effective sizing |
|---|---|---|
| No chunking env variables set | `CHUNKING_PRESET=balanced`, `CHUNKING_STRATEGY=auto` | `CHUNK_SIZE=1200`, `CHUNK_OVERLAP=120` |
| `CHUNKING_PRESET=balanced` | `CHUNKING_STRATEGY=auto` unless explicitly overridden | balanced defaults unless `CHUNK_SIZE` / `CHUNK_OVERLAP` are explicitly set |
| `CHUNKING_PRESET=legacy` | `CHUNKING_STRATEGY=recursive` unless explicitly overridden | `CHUNK_SIZE=1500`, `CHUNK_OVERLAP=100` unless explicitly set |
| Explicit `CHUNKING_STRATEGY` | explicit strategy wins over preset-derived strategy | preset sizing unless explicitly set |
| Explicit `CHUNK_SIZE` / `CHUNK_OVERLAP` | no strategy change | explicit size / overlap always win |

Changing the unset-env runtime default from recursive `1500/100` to balanced `auto` with `1200/120` is intentional for the MVP, but operators must be able to roll back by setting `CHUNKING_PRESET=legacy` or `CHUNKING_STRATEGY=recursive`.

## 8. Strategy Behavior

### 8.1 `recursive`

This is the existing behavior.

Use `RecursiveCharacterTextSplitter` with `CHUNK_SIZE` and `CHUNK_OVERLAP`.

This strategy must remain available for compatibility and rollback.

### 8.2 `paragraph`

Paragraph-aware chunking treats paragraphs as preferred boundaries.

Rules:

- Paragraphs should be grouped into chunks near `CHUNK_SIZE`.
- A paragraph should not be split unless it exceeds `MAX_CHUNK_SIZE`.
- Very small chunks should be merged forward when possible.
- Long paragraphs should fallback to recursive splitting.

This strategy is useful for plain text, prose-style DOCX, EPUB, and text-based PDFs.

### 8.3 `auto`

`auto` is the balanced default.

It should use document structure when available and fallback to recursive splitting when structure cannot be preserved.

Rules:

- Headings should stay close to their following content.
- Paragraphs are preferred boundaries.
- Small tables should stay intact where possible.
- Large tables should be split by row ranges when possible, not by raw character splitting.
- Code blocks should not be split unless too large.
- PDF page metadata should be preserved.
- XLSX/CSV sheet and row metadata should be preserved where possible.
- PPT/PPTX slide metadata should be preserved where possible.
- Deterministic contextual prefixes should be added when useful metadata is available.
- If structure-aware processing fails, fallback to recursive splitting and mark the chunk strategy as `recursive_fallback`.

## 9. Contextual Retrieval Alignment

This proposal adopts a lightweight, deterministic subset of contextual retrieval.

The MVP does **not** call an LLM to generate context for every chunk. Instead, it derives a compact contextual prefix from metadata already available from loaders and normalizers, such as filename, heading path, page range, slide range, sheet name, and row range.

This keeps ingestion deterministic, inexpensive, easier to test, and more suitable for an upstream PR.

Example contextual prefixes:

```text
[Context: file=README.md; section=Installation > Docker > Environment Variables]

Actual chunk content...
```

```text
[Context: file=contract.pdf; pages=4-5]

Actual chunk content...
```

```text
[Context: file=report.xlsx; sheet=Revenue; rows=20-45]

Actual chunk content...
```

Rules:

- Only include fields that are available and useful.
- Keep the prefix compact and deterministic.
- The prefix must count toward final chunk size.
- The prefix should be added to the text that is embedded and stored.
- The original metadata should also be stored separately where possible.
- If no useful contextual metadata exists, no prefix should be added.
- The prefix must be sanitized and capped by `MAX_CONTEXTUAL_PREFIX_LENGTH`.
- The prefix should be added only once per chunk.

## 10. Loader Dependency and Graceful Degradation

The `auto` strategy depends on the metadata and structure exposed by existing document loaders.

Examples:

- PDF page metadata is only available if the PDF loader exposes page-level metadata.
- DOCX heading metadata is only available if the loader or normalizer can detect heading styles or heading-like structure.
- Spreadsheet row metadata is only available if the loader or normalizer can preserve sheet and row information.
- PPT/PPTX slide metadata is only available if the loader output can be mapped to slides.

If a loader does not expose enough structure, the chunker should gracefully degrade:

```text
structure-aware
  -> paragraph-aware when text blocks are usable
  -> recursive_fallback when structure cannot be preserved safely
```

This fallback should keep ingestion successful whenever recursive splitting succeeds.

## 11. Loader Metadata Contract

Structure-aware metadata must only be emitted when the loader output or an explicit normalizer can derive it reliably. If a field cannot be derived with stable behavior for a file type, that field should be omitted and the chunker should continue with paragraph-aware or recursive fallback behavior.

Current loader/normalizer expectations:

| File type | Current loader considerations | MVP contract |
|---|---|---|
| PDF | `PyPDFLoader`-based loading commonly exposes page metadata. | A PDF normalizer must map page metadata into `page_start` / `page_end` and enforce the 2-page span limit before page-span metadata is required. |
| Markdown | `UnstructuredMarkdownLoader` may expose elements, but heading hierarchy should not be assumed without normalization. | A Markdown normalizer must derive `heading_path` / `section_title` before heading-aware acceptance criteria apply. |
| DOC/DOCX | `Docx2txtLoader` primarily exposes flattened text and may not preserve heading styles or tables. | DOCX heading and table behavior is best-effort until a normalizer or loader change can prove stable heading/table extraction. |
| CSV | `CSVLoader` can expose row-like documents but exact row metadata may vary. | A CSV normalizer must provide `row_start` / `row_end` and header repetition before row-aware acceptance criteria apply. |
| XLS/XLSX | `UnstructuredExcelLoader` output may not reliably preserve sheet and row ranges in a normalized form. | Spreadsheet sheet/row behavior is best-effort until a normalizer can reliably derive `sheet_name`, `row_start`, and `row_end`. |
| PPT/PPTX | `UnstructuredPowerPointLoader` may expose slide-like elements, but slide range mapping should not be assumed. | Slide-aware behavior is best-effort until a normalizer can reliably derive `slide_start` / `slide_end`. |
| JSON | Current path is text-based. | Keep text-based behavior initially and emit only generic chunk metadata unless a future JSON-aware normalizer is added. |
| XML/XHTML/RST/EPUB | Unstructured loaders may expose blocks but metadata stability varies by dependency/runtime. | Use paragraph/block-aware behavior when usable; omit section/page fields unless a normalizer derives them reliably. |
| Source/text files | `TextLoader` preserves text but not structural code metadata. | Emit optional `char_start` / `char_end` only when the chunker can calculate them deterministically. |

Normalizers introduced by this work should have small, testable contracts. A normalizer that claims support for heading paths, page spans, row ranges, sheet names, or slide ranges must include unit tests for both successful metadata derivation and graceful omission when the loader output lacks enough structure.

## 12. File Type Behavior

| File type | Strategy behavior | Metadata / prefix inputs |
|---|---|---|
| PDF | Page-aware + paragraph-aware. Chunks may span up to 2 adjacent pages when useful, but the 2-page span is a maximum boundary, not a target; `MAX_CHUNK_SIZE` remains the primary size constraint. | `filename`, `page_start`, `page_end` |
| Markdown | Heading-aware, paragraph-aware, preserve fenced code blocks where possible. | `filename`, `heading_path`, `section_title` |
| DOC/DOCX | Heading-aware when styles are available; otherwise paragraph-aware. Tables should be preserved where possible. | `filename`, `heading_path`, `section_title` |
| TXT / `text/*` | Paragraph-aware with simple section detection when possible. | `filename`, `section_title`, optional `char_start`, `char_end` |
| CSV | Row-aware; repeat header row in each chunk where possible. | `filename`, `row_start`, `row_end` |
| XLS/XLSX | Sheet/row-aware; repeat header row in each chunk where possible. | `filename`, `sheet_name`, `row_start`, `row_end` |
| PPT/PPTX | Slide-aware. Each slide is a natural boundary. | `filename`, `slide_start`, `slide_end` |
| JSON | Keep current text-based behavior initially; fallback recursive. | `filename`, existing metadata + chunk metadata |
| XML/XHTML/RST/EPUB | Paragraph/block-aware when loader output is usable; fallback recursive. | `filename`, section/page metadata where available |
| Source/text files | Paragraph/code-aware when practical; fallback recursive. | `filename`, optional `char_start`, `char_end` |

## 13. Table Handling

Tables should be treated as atomic blocks only when they fit within `MAX_CHUNK_SIZE`.

For small tables:

- keep the table intact where possible;
- preserve the surrounding section/page/sheet metadata;
- avoid splitting through the table body.

For large tables:

- split by row ranges instead of raw character ranges when possible;
- repeat the header row in each table chunk;
- preserve `sheet_name`, `row_start`, and `row_end` metadata when available;
- sanitize and cap repeated header rows so they do not consume an excessive share of the chunk budget;
- if the header row alone exceeds the available chunk budget after sanitization, use a compact column summary or fallback table-text representation with metadata preserved;
- use recursive fallback only if row-aware splitting is not possible.

Example table chunk content:

```text
[Context: file=report.xlsx; sheet=Revenue; rows=20-45]

| Month | Region | Revenue |
|---|---|---|
| Jan | APAC | 10000 |
...
```

This avoids losing the relationship between column headers and row values during embedding.

## 14. Metadata

Metadata changes must be additive-only.

System-owned metadata keys must be preserved and must take precedence over loader-provided metadata:

```text
file_id
user_id
digest
```

If loader metadata contains one of these protected keys, the system-owned value wins. The conflicting loader value should be dropped, or moved to a clearly loader-scoped key only if it is useful and safe after sanitization. Loader metadata must be sanitized before insertion into the final metadata object.

New metadata fields may include:

```json
{
  "filename": "policy.pdf",
  "mime_type": "application/pdf",
  "chunk_index": 12,
  "chunk_strategy": "auto",
  "chunking_preset": "balanced",
  "chunk_size": 1180,
  "context_prefix": "[Context: file=policy.pdf; pages=3-4]",
  "contextual_prefix_enabled": true,
  "page_start": 3,
  "page_end": 4,
  "heading_path": ["Installation", "Docker"],
  "section_title": "Docker",
  "slide_start": 2,
  "slide_end": 2,
  "sheet_name": "Revenue",
  "row_start": 20,
  "row_end": 45,
  "char_start": 1200,
  "char_end": 2380
}
```

Not every field is required for every file type.

### Digest and Vector ID Semantics

`digest` should be generated from the final chunk text that is embedded and stored. When contextual prefixes are enabled, the prefix is part of that text and therefore part of the digest.

This means changing contextual prefix settings, prefix format, or chunking strategy may change `digest` values and any vector-store IDs derived from `file_id` + `digest`, such as Atlas Mongo document IDs. With `CHUNKING_STRATEGY=recursive` and contextual prefixes disabled, digest behavior should remain compatible with the current implementation.

## 15. Metadata Sanitization

Metadata must remain compact and safe for vector store insertion.

Rules:

- Metadata changes are additive-only.
- System-owned keys such as `file_id`, `user_id`, and `digest` must be preserved and must not be overwritten by loader metadata.
- Loader metadata must be sanitized before merge; unsupported or unsafe values should be dropped before vector store insertion.
- Loader metadata that conflicts with protected system-owned keys should be dropped or renamed to a loader-scoped key only when useful.
- `heading_path` should be truncated to `MAX_HEADING_PATH_DEPTH`, default `4`.
- Individual metadata string values should be truncated to `MAX_METADATA_STRING_LENGTH`, default `512` characters.
- `context_prefix` should be truncated to `MAX_CONTEXTUAL_PREFIX_LENGTH`, default `240` characters.
- Unsupported, unserializable, or excessively nested metadata values should be dropped.
- Metadata sanitization must not mutate the original document content.

The goal is to avoid vector store insert failures caused by very large or unserializable metadata payloads.

## 16. Deterministic Contextual Prefix Format

When contextual metadata is available, prepend a compact deterministic prefix into the chunk text.

Preferred format:

```text
[Context: file=README.md; section=Installation > Docker > Environment Variables]

Actual chunk content...
```

For Markdown and DOCX, this supersedes the older heading-only prefix format:

```text
[Section: Installation > Docker > Environment Variables]
```

The `[Section: ...]` format remains acceptable as an implementation detail for heading-only chunks, but `[Context: ...]` is preferred because it generalizes to PDFs, spreadsheets, slides, and other file types.

Rules:

- The contextual prefix length must be included in final chunk size calculation.
- The chunker should reserve space for the prefix before adding body content.
- Overlap should be calculated from body content only; the contextual prefix should be generated once per final chunk after overlap selection.
- The prefix should include only available fields.
- The prefix should be deterministic and testable.
- The prefix should be sanitized before insertion.
- Empty or unavailable context should not create an empty prefix.
- The prefix should be added only once per chunk.
- The prefix should be capped by `MAX_CONTEXTUAL_PREFIX_LENGTH`, default `240` characters.

## 17. Failure Behavior

When `CHUNKING_STRATEGY=auto` fails to preserve structure:

1. Log a warning.
2. Fallback to recursive splitting.
3. Keep upload/indexing successful if recursive splitting succeeds.
4. Mark metadata with:

```json
{
  "chunk_strategy": "recursive_fallback"
}
```

Unknown strategies should also fallback to `recursive` with a warning.

## 18. Implementation Notes

The implementation should introduce a dedicated chunking service/module instead of keeping chunking logic directly in route handlers.

Suggested high-level structure:

```text
app/services/chunking/
  recursive.py
  paragraph.py
  auto.py
  metadata.py
  factory.py
```

The exact class/interface design is intentionally left flexible for upstream review.

The route-level ingestion flow should call a chunking service, for example:

```text
loader output -> chunking service -> normalized chunks -> embed -> vector store
```

## 19. Observability

Add logs for:

- selected chunking strategy;
- file id;
- filename or mime type when available;
- input document count;
- output chunk count;
- average chunk size;
- fallback events;
- contextual prefix insertion/truncation events;
- metadata sanitization events;
- table row-splitting events;
- chunking duration.

Debug logs may include sample metadata for the first few chunks, but must not log full document content by default.

## 20. Test Plan

### Unit tests

Cover:

- recursive strategy compatibility;
- character-based sizing semantics;
- paragraph grouping;
- long paragraph fallback;
- contextual prefix size accounting;
- deterministic contextual prefix formatting;
- auto fallback to `recursive_fallback`;
- metadata normalization;
- metadata sanitization;
- protected metadata collision handling, ensuring system-owned `file_id`, `user_id`, and `digest` win over loader metadata;
- PDF page span limit;
- Markdown/DOCX heading context formatting;
- large table row-aware splitting when row metadata is available;
- oversized table header sanitization, capping, and fallback behavior;
- body-only overlap behavior when contextual prefixes are enabled;
- config precedence for unset env, `balanced`, `legacy`, explicit strategy, and explicit sizing;
- digest compatibility with contextual prefixes disabled;
- digest changes when contextual prefixes are enabled and included in stored chunk text;
- loader-normalizer best-effort behavior when structure metadata is unavailable.

### Integration tests

Cover:

- `/embed` still works;
- `/local/embed` still works;
- `/embed-upload` still works;
- `/query` still works;
- `/query_multiple` still works;
- `/text` still returns raw extracted text without contextual prefix or chunk embedding behavior;
- delete by `file_id` still works;
- existing authorization behavior remains unchanged.

## 21. Rollout / PR Plan

### PR 1: Chunking service refactor + balanced preset

- Move existing recursive splitting into a chunking service.
- Add `CHUNKING_PRESET` and `CHUNKING_STRATEGY`.
- Add `legacy` and `balanced` configurations.
- Clarify character-based chunk sizing.
- Keep recursive fallback.
- Preserve API contract.

### PR 2: Metadata normalization + core auto behavior

- Add additive metadata fields.
- Add metadata sanitization.
- Add loader metadata inventory and explicit normalizer contracts for reliable structure-aware fields.
- Add paragraph-aware behavior.
- Add PDF page-aware metadata with max 2-page span.
- Add deterministic contextual prefix support for PDF, Markdown, DOCX, spreadsheet, and slide metadata where available.
- Ensure contextual prefix length is counted toward final chunk size.
- Add recursive fallback behavior for failed `auto` chunking.

### PR 3: Office and structured file refinements

- Improve DOC/DOCX heading-aware behavior where loader output allows.
- Improve CSV/XLS/XLSX row/sheet-aware behavior.
- Add large table row-aware splitting and header repetition.
- Improve PPT/PPTX slide-aware behavior.
- Improve XML/RST/EPUB/source/text fallback handling.

## 22. Acceptance Criteria

The implementation is acceptable when:

1. LibreChat's existing RAG API integration continues to work without API changes.
2. `recursive` strategy remains available and behaves like the current implementation.
3. Chunk sizing variables are clearly character-based.
4. `balanced` preset uses `auto` by default.
5. `auto` falls back to recursive with warning and `chunk_strategy=recursive_fallback` when needed.
6. Metadata changes are additive-only.
7. Metadata is sanitized before vector store insertion, and protected system-owned metadata keys cannot be overwritten by loader metadata.
8. PDF chunks never span more than 2 adjacent pages in MVP, and the page span limit is treated as a ceiling while `MAX_CHUNK_SIZE` remains the primary size constraint.
9. Deterministic contextual prefixes are prepended when useful metadata is available.
10. Contextual prefix length is included in final chunk size calculation.
11. When contextual prefixes are enabled, overlap is calculated from body content only and prefixes are generated once per final chunk.
12. Large tables are split by row ranges where possible, with sanitized/capped header context repeated in each chunk.
13. Structure-aware metadata fields are emitted only when loader output or a normalizer can derive them reliably.
14. `digest` is generated from the final stored and embedded chunk text, including contextual prefix when enabled.
15. Existing `/embed`, `/local/embed`, `/embed-upload`, `/query`, `/query_multiple`, `/text`, and delete flows remain functional.
16. `/text` remains raw text extraction and does not add contextual prefixes or embedding-only chunk metadata.
17. Existing authorization behavior remains unchanged.
18. Parent-child retrieval, preview endpoint, and re-index endpoint are not included in MVP.

## 23. Future Work

Future work may include:

- LLM-generated per-chunk contextualization.
- Contextual BM25 / lexical indexing over contextualized chunk text.
- Hybrid vector + keyword search.
- Reranking.
- Parent-child retrieval.
- OCR for scanned PDFs.
- Token-based chunk sizing.
- More precise source citation formatting.
- Code-aware chunking by function/class.
- Optional re-index tooling.
