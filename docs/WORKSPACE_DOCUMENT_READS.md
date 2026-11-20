# Reading documents with the workspace agent

The workspace agent's read-only `fs.read` tool accepts text files,
PDF (`.pdf`), Word (`.docx`) and Excel (`.xlsx` or `.xlsm`)
files under the workspace root. Files remain subject to the existing path
confinement and VCS-metadata exclusions. Legacy binary `.doc` and
`.xls` are not supported; save those as `.docx` or `.xlsx`.

It returns extracted **text**, not the original binary. PDF output is labeled
by page number. Word paragraphs and table rows are kept in source order.
Excel output is labeled by worksheet, with cell coordinates and values;
formulas are shown as text when a cached value is unavailable. Chartsheets are
skipped when reading a workbook; worksheet indices count only data worksheets. Excel macros,
formulas and external links are never executed or fetched. PDF image-only
pages have no extractable text (OCR is not performed). Word documents have no
stable intrinsic page boundaries, so page selection is PDF-only.

## Selection arguments

These are additive to the existing `path`, `offset` (0-based
**text line**) and `limit` (**text lines**) arguments. Page and sheet
indices are **1-based and inclusive**; absent starts default to the first
page/sheet and absent ends default to the last.

| Argument | File type | Purpose |
| --- | --- | --- |
| `page_start`, `page_end` | PDF | Read a page interval |
| `sheet_start`, `sheet_end` | Excel | Read a worksheet-index interval |
| `sheet` | Excel | Read one worksheet by name (alternative to indices) |
| `offset`, `limit` | All | Window the resulting text by lines |

Example calls:

```json
{"path": "reports/study.pdf", "page_start": 5, "page_end": 8}
{"path": "reports/findings.docx", "offset": 0, "limit": 100}
{"path": "budgets/forecast.xlsx", "sheet": "Assumptions"}
{"path": "budgets/forecast.xlsx", "sheet_start": 2, "sheet_end": 4}
```

## Resource limits

Existing workspace-tool caps are unchanged: the source file is limited to
**8 MiB** (bounded before parser invocation) and a single `fs.read` response
to **40,000 characters**, with the existing truncation marker. PDF pages
retain the attachment parser's page-count and compressed-stream safeguards;
Office ZIP containers are checked for total expansion and oversized XML
parts before parsing. Extracted document text is also limited to
**1,000,000 characters**. Invalid ranges, corrupt files, and oversized
documents return ordinary tool errors rather than crashing the agent.
