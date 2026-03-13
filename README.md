# GoAI API Documentation Interpreter

## Project purpose
GoAI API Documentation Interpreter is a production-oriented V1 internal Streamlit tool focused on one workflow:
**documentation -> endpoint extraction -> validation -> review -> test -> Postman export**.

## Features
- Session-only OpenAI API key input (never persisted by design).
- Multi-file documentation ingestion (PDF, DOCX, XLSX, TXT, PNG/JPG/JPEG).
- OpenAI-based extraction into a strict normalized schema.
- GoFlow compatibility validation and status classification.
- Editable API details panel for human review.
- Manual in-app API test runner using `requests`.
- Postman collection and environment export for selected APIs.
- Multi-language UI support (English, Spanish, Portuguese).

## Supported file types
- PDF (text extraction)
- DOCX (text extraction)
- XLSX (sheet-to-text transformation)
- TXT (plain text)
- PNG/JPG/JPEG (accepted upload; OCR not implemented in V1)

## Supported languages
- English (default)
- Spanish
- Portuguese

## Security behavior
- OpenAI key is only used in active Streamlit session state.
- No persistent secret storage (no DB, file, browser persistence).
- Sensitive headers are masked in test response display.
- Export uses placeholders by default.
- Optional export checkbox includes current session values for that export action only.
- Basic likely-credential detection warns user during analysis.

## How OpenAI key is handled
- Entered via password input field.
- Used only for OpenAI API calls during active session.
- Not written to disk, logs, cache, or local storage.
- Field appears blank on fresh app start.

## Run locally
1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start app:
   ```bash
   streamlit run app.py
   ```

## How to use the app
1. Select UI language.
2. Provide your OpenAI API key.
3. Upload documentation files and/or paste raw documentation text.
4. Click **Analyze Documentation**.
5. Review extracted API rows in the table.
6. Select an API and edit details in API Detail Editor.
7. Use API Test Runner for manual request execution.
8. Choose APIs and export Postman collection + environment JSON.

## Export behavior
- Exports only selected APIs.
- Collection file: `collection.postman_collection.json`.
- Environment file: `environment.postman_environment.json`.
- Includes only variables used by selected requests.
- Uses placeholders by default, with optional checkbox for current session values.

## Known V1 limitations
- Image OCR is intentionally not implemented.
- OpenAI extraction quality depends on documentation quality and model output strictness.
- No database persistence or collaborative editing.
- No advanced Postman scripting support.
- No OAuth flow orchestration.

## Dockerize later
Docker is optional in V1. To dockerize later:
- Use a Python 3.11 slim base image.
- Copy project files and install `requirements.txt`.
- Expose Streamlit port `8501`.
- Run `streamlit run app.py --server.address=0.0.0.0 --server.port=8501`.
