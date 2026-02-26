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

    # Or OpenAI
    export OPENAI_API_KEY="your-key-here"
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

-   **LLM Choice**: Supports Nebius (`meta-llama/Meta-Llama-3.1-70B-Instruct`) or OpenAI (`gpt-4.1-mini`) via environment variables. The prompt is direct and asks for a raw JSON response.
