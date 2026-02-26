# Repo Summarizer v2

FastAPI service to summarize a GitHub repo using an LLM.

## Quick Start

1.  **Clone & Install**

    ```bash
    git clone <this-repo-url>
    cd github-repo-summarizer-v2
    pip install -r requirements.txt
    ```

2.  **Set API Key**

    ```bash
    # Use Nebius (preferred)
    export NEBIUS_API_KEY="your-key-here"
    export NEBIUS_MODEL="your-model-id"

    # Or OpenAI
    export OPENAI_API_KEY="your-key-here"
    ```

3.  **Find a Valid Nebius Model (TokenFactory)**

    ```bash
    python - <<'PY'
    import os
    from openai import OpenAI
    client = OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/", api_key=os.environ.get("NEBIUS_API_KEY"))
    print([m.id for m in client.models.list().data])
    PY
    ```

3.  **Run Server**

    ```bash
    uvicorn main:app --port 8000
    ```

4.  **Test**

    ```bash
    curl -X POST http://localhost:8000/summarize \
      -H "Content-Type: application/json" \
      -d '{"github_url": "https://github.com/fastapi/fastapi"}'
    ```

## API

`POST /summarize`

-   **Body**: `{"github_url": "<url>"}`
-   **Success**: `200 OK` with `{"summary": "...", "technologies": [...], "structure": "..."}`
-   **Errors**: `400` (bad URL), `404` (not found), `500` (server error), `502` (API/LLM error)

## Design Notes

-   **Code Style**: This version is a rewrite of the original, aiming for a more compact and pragmatic style. It consolidates the logic into two files (`main.py`, `github.py`) and uses more terse naming conventions.

-   **Repo Processing**: The strategy remains the same: use the GitHub Trees API, filter out junk, prioritize key files, and assemble a context string within a token budget. This avoids cloning the repo and provides the LLM with a high-quality snapshot.

-   **LLM Choice**: Supports Nebius or OpenAI via environment variables. Nebius uses the TokenFactory endpoint and requires a valid model ID from `client.models.list()`.
-   **No Hardcoded Keys**: API keys are read from environment variables and are not stored in code or config files. Use `.env.example` as a template and keep your real `.env` out of version control.
