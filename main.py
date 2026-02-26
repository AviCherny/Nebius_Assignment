#!/usr/bin/env python3
"""
GitHub Repo Summarizer – single-file API server.

Run:  uvicorn main:app --port 8000
Env:  NEBIUS_API_KEY or OPENAI_API_KEY
"""

import json, os, re, logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from github import grab_repo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("summarizer")

# ── app ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Repo Summarizer", version="2.0.0")

class Req(BaseModel):
    github_url: str = Field(..., examples=["https://github.com/psf/requests"])

class Resp(BaseModel):
    summary: str
    technologies: list[str]
    structure: str

class Err(BaseModel):
    status: str = "error"
    message: str


# ── llm setup ───────────────────────────────────────────────────────────────

NEBIUS_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_MODEL = os.environ.get("NEBIUS_MODEL") or "meta-llama/Meta-Llama-3.1-8B-Instruct"
OPENAI_MODEL = "gpt-4.1-mini"

def _llm() -> tuple[AsyncOpenAI, str]:
    nk = os.environ.get("NEBIUS_API_KEY")
    if nk:
        log.info("LLM provider: Nebius")
        return AsyncOpenAI(api_key=nk, base_url=NEBIUS_URL), NEBIUS_MODEL
    if os.environ.get("OPENAI_API_KEY"):
        log.info("LLM provider: OpenAI")
        return AsyncOpenAI(), OPENAI_MODEL          # reads env automatically
    raise EnvironmentError("Set NEBIUS_API_KEY or OPENAI_API_KEY")


SYSTEM = (
    "You are a senior engineer analyzing a GitHub repository. "
    "Given the repo contents below, return a JSON object with exactly three keys:\n\n"
    '  "summary"       – 2-5 sentence description of what the project does (use **bold** for the project name)\n'
    '  "technologies"  – list of main languages, frameworks, and tools (3-10 items, ordered by importance)\n'
    '  "structure"     – 2-4 sentence description of the directory layout\n\n'
    "Rules:\n"
    "- Return ONLY raw JSON. No markdown fences, no commentary.\n"
    "- Be factual. Only mention what you can confirm from the files.\n"
    "- If unsure, omit rather than guess."
)


async def _ask_llm(context: str) -> dict:
    client, model = _llm()
    prompt = f"Analyze this repository:\n\n{context}\n\nReturn JSON only."
    log.info(f"Sending {len(prompt)} chars to {model}")

    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content.strip()
    log.info(f"LLM replied with {len(raw)} chars")
    try:
        return _parse(raw)
    except Exception:
        log.warning("LLM returned invalid JSON; requesting repair")
        repair = (
            "Your previous response was invalid JSON. "
            "Return ONLY valid JSON that matches the required schema. "
            "Do not include markdown or extra text.\n\n"
            f"Bad response:\n{raw}"
        )
        resp2 = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": repair}],
            temperature=0.0,
            max_tokens=2000,
        )
        raw2 = resp2.choices[0].message.content.strip()
        log.info(f"LLM repair replied with {len(raw2)} chars")
        return _parse(raw2)


def _parse(txt: str) -> dict:
    # strip code fences if the model wrapped it
    t = txt.strip()
    if t.startswith("```"):
        t = t[t.index("\n")+1:] if "\n" in t else t[3:]
        if t.endswith("```"): t = t[:-3]
        t = t.strip()

    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", txt)
        if m:
            data = json.loads(m.group())
        else:
            raise ValueError(f"LLM returned unparseable response: {txt[:200]}")

    # normalize types
    s = data.get("summary", "")
    tech = data.get("technologies", [])
    st = data.get("structure", "")
    if not isinstance(s, str): s = str(s)
    if not isinstance(st, str): st = str(st)
    if not isinstance(tech, list): tech = [str(tech)]
    else: tech = [str(x) for x in tech]
    if not s:
        raise ValueError("LLM produced empty summary")
    return {"summary": s, "technologies": tech, "structure": st}


# ── url check ───────────────────────────────────────────────────────────────

GH_RE = re.compile(r"^https?://github\.com/[\w.\-]+/[\w.\-]+(/?|\.git)?$")

def _bad_url(u: str) -> str | None:
    if not u: return "github_url must not be empty."
    if not GH_RE.match(u): return "Invalid GitHub URL. Expected https://github.com/OWNER/REPO"
    return None


# ── endpoint ────────────────────────────────────────────────────────────────

@app.post("/summarize", response_model=Resp,
          responses={400: {"model": Err}, 404: {"model": Err},
                     500: {"model": Err}, 502: {"model": Err}})
async def summarize(req: Req):
    url = req.github_url.strip()

    err = _bad_url(url)
    if err:
        raise HTTPException(400, {"status": "error", "message": err})

    # fetch repo
    try:
        ctx = await grab_repo(url)
    except ValueError as e:
        raise HTTPException(400, {"status": "error", "message": str(e)})
    except PermissionError as e:
        raise HTTPException(403, {"status": "error", "message": str(e)})
    except FileNotFoundError as e:
        raise HTTPException(404, {"status": "error", "message": str(e)})
    except RuntimeError as e:
        raise HTTPException(502, {"status": "error", "message": str(e)})
    except Exception as e:
        log.exception("Repo fetch blew up")
        raise HTTPException(500, {"status": "error", "message": f"Repo fetch failed: {e}"})

    if not ctx.strip():
        raise HTTPException(400, {"status": "error",
                                  "message": "Repo is empty or has no readable files."})

    # summarize
    try:
        result = await _ask_llm(ctx)
    except EnvironmentError as e:
        raise HTTPException(500, {"status": "error", "message": str(e)})
    except Exception as e:
        log.exception("LLM call failed")
        raise HTTPException(502, {"status": "error", "message": f"LLM error: {e}"})

    return Resp(**result)
