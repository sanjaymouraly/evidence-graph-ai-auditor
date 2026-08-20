# Evidence-Graph AI Auditor for Business Decisions

An evidence-graph auditor for tracing business decisions back to supporting evidence.

## Features

- Extracts claims from business documents and links each one to a typed evidence span.
- Builds an evidence graph with locators for PDFs, DOCX, spreadsheets, email, and text.
- Surfaces conflicting evidence instead of hiding it behind a single score.
- Runs deterministic decision gates whose inputs and result hash are recorded.
- Restricts AI models to narration over retrieved claims and validates every citation.
- Exports an auditable decision record with provenance and reviewer actions.

## Stack

Python, Node.js, FastAPI, Uvicorn, SQLAlchemy, Pydantic, pypdf, pdfplumber, openpyxl, ReportLab

## Clone

```bash
git clone https://github.com/sanjaymouraly/evidence-graph-ai-auditor.git
cd evidence-graph-ai-auditor
```

## Setup

```bash
# The project ships a Docker Compose setup.
docker compose up --build
```

See `docs/` or the compose file for environment variables and ports.

## Run

```bash
docker compose up --build
```

## Tests

```bash
pytest -q
```

## Notes

- Sample data, when included, is synthetic.

## License

MIT © 2026 [Sanjay Mouraly](https://github.com/sanjaymouraly)
