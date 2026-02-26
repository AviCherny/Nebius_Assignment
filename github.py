"""Fetch + process GitHub repos for LLM consumption."""

import asyncio, os, re, subprocess, logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

log = logging.getLogger("github")

# ── config ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Config:
    api_base: str = "https://api.github.com"
    ctx_budget: int = 100_000
    file_cap: int = 15_000
    max_fetch: int = 40
    concurrency: int = 3
    big_file: int = 100_000

CFG = Config()

# ── junk filters ────────────────────────────────────────────────────────────

JUNK_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", ".venv", "venv", "env", ".env", "vendor", "dist",
    "build", ".next", ".nuxt", "out", "target", ".idea", ".vscode",
    ".gradle", ".cache", ".eggs", "egg-info", "site-packages",
    "coverage", ".coverage", "htmlcov", ".terraform", ".serverless",
})

JUNK_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".tiff", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pyc", ".pyo",
    ".so", ".o", ".a", ".dylib", ".dll", ".exe", ".class", ".jar",
    ".war", ".whl", ".egg", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".rar", ".7z", ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".ogg", ".bin", ".dat", ".db", ".sqlite", ".sqlite3", ".pdf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".lock", ".map",
    ".min.js", ".min.css",
})

JUNK_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "gemfile.lock", "pipfile.lock", "poetry.lock", "composer.lock",
    "cargo.lock", ".ds_store", "thumbs.db", ".gitattributes",
})

# files that tell you the most about a project
KEY_FILES = frozenset({
    "readme", "readme.md", "readme.rst", "readme.txt",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "cargo.toml", "go.mod", "go.sum", "build.gradle", "pom.xml",
    "gemfile", "mix.exs", "project.clj", "makefile", "cmakelists.txt",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "requirements.txt", "environment.yml",
    "tsconfig.json", "vite.config.ts", "vite.config.js",
    "webpack.config.js", "rollup.config.js", "next.config.js",
    "next.config.mjs", "nuxt.config.ts", "angular.json",
    "license", "license.md", "license.txt",
    "contributing.md", "changelog.md",
})

BORING_DIRS = frozenset({
    "test", "tests", "spec", "specs", "__tests__", "examples",
    "example", "samples", "sample", "docs", "doc", "documentation",
    ".github", ".circleci", ".gitlab", "scripts", "tools", "benchmarks",
})

OK_DOTFILES = frozenset({
    ".env.example", ".dockerignore", ".gitignore", ".eslintrc.js",
    ".eslintrc.json", ".prettierrc", ".babelrc", ".editorconfig",
})


# ── public interface ────────────────────────────────────────────────────────

async def grab_repo(url: str) -> str:
    """Main entry: URL in, context string out."""
    owner, repo = _crack_url(url)
    log.info(f"Processing {owner}/{repo}")

    hdrs = _headers()
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CFG.concurrency)) as http:
        meta = await _meta(http, hdrs, owner, repo)
        tree = await _tree(http, hdrs, owner, repo, meta["branch"])
        ranked = _classify(tree)
        if not ranked:
            return ""

        # always start with the dir listing
        dir_txt = _render_tree([n["path"] for n in tree if n["type"] == "blob"])
        parts = [f"## Directory Tree\n\n```\n{dir_txt}\n```\n"]
        used = len(parts[0])

        if meta["desc"]:
            blk = f"## Repository Description\n\n{meta['desc']}\n"
            parts.append(blk); used += len(blk)
        if meta["lang"]:
            blk = f"## Primary Language\n\n{meta['lang']}\n"
            parts.append(blk); used += len(blk)

        pick = _budget_pick(ranked, CFG.ctx_budget - used)
        log.info(f"Fetching {len(pick)}/{len(ranked)} files")

        contents = await _batch_fetch(http, hdrs, owner, repo, meta["branch"], pick)

        for f in pick:
            if used >= CFG.ctx_budget:
                break
            body = contents.get(f["path"])
            if not body:
                continue
            if len(body) > CFG.file_cap:
                body = body[:CFG.file_cap] + "\n\n... [truncated]"
            section = f"## File: {f['path']}\n\n```\n{body}\n```\n"
            room = CFG.ctx_budget - used
            if len(section) > room:
                section = f"## File: {f['path']}\n\n```\n{body[:max(0,room-200)]}\n... [truncated]\n```\n"
                parts.append(section); used += len(section)
                break
            parts.append(section); used += len(section)

    log.info(f"Context ready: {used} chars, {len(parts)-1} file sections")
    return "\n".join(parts)


# ── url parsing ─────────────────────────────────────────────────────────────

def _crack_url(url: str) -> tuple[str, str]:
    m = re.match(r"https?://github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise ValueError(f"Bad GitHub URL: '{url}'. Need https://github.com/OWNER/REPO")
    return m.group(1), m.group(2)


# ── auth ────────────────────────────────────────────────────────────────────

def _token() -> str | None:
    t = os.environ.get("GITHUB_TOKEN")
    if t: return t
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None

def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-repo-summarizer",
    }
    tok = _token()
    if tok: h["Authorization"] = f"Bearer {tok}"
    return h

def _rate_limit_details(r: httpx.Response) -> str:
    remaining = r.headers.get("X-RateLimit-Remaining")
    limit = r.headers.get("X-RateLimit-Limit")
    reset = r.headers.get("X-RateLimit-Reset")
    resource = r.headers.get("X-RateLimit-Resource")
    retry_after = r.headers.get("Retry-After")
    parts: list[str] = []
    if remaining is not None: parts.append(f"remaining={remaining}")
    if limit is not None: parts.append(f"limit={limit}")
    if resource is not None: parts.append(f"resource={resource}")
    if reset and reset.isdigit():
        dt = datetime.fromtimestamp(int(reset), tz=timezone.utc)
        parts.append(f"reset_utc={dt.isoformat()}")
    if retry_after is not None: parts.append(f"retry_after={retry_after}s")
    return " ".join(parts)

def _maybe_rate_limit(r: httpx.Response, ctx: str) -> PermissionError | None:
    if r.status_code in (403, 429):
        txt = (r.text or "").lower()
        if "rate limit" in txt or r.headers.get("X-RateLimit-Remaining") == "0":
            details = _rate_limit_details(r)
            msg = f"GitHub rate limit hit during {ctx}."
            if details: msg = f"{msg} {details}"
            return PermissionError(msg)
    return None


# ── github api calls ───────────────────────────────────────────────────────

async def _meta(http, hdrs, owner, repo) -> dict:
    r = await http.get(f"{CFG.api_base}/repos/{owner}/{repo}", headers=hdrs)
    rl = _maybe_rate_limit(r, "repo metadata fetch")
    if rl: raise rl
    if r.status_code == 404:
        raise FileNotFoundError(f"Repo '{owner}/{repo}' not found or is private.")
    if r.status_code == 403:
        raise PermissionError(f"Access denied for '{owner}/{repo}'. Possibly private or rate-limited.")
    if r.status_code != 200:
        raise RuntimeError(f"GitHub API returned {r.status_code}: {r.text[:300]}")
    d = r.json()
    return {"branch": d.get("default_branch", "main"),
            "desc": d.get("description", ""),
            "lang": d.get("language", "")}

async def _tree(http, hdrs, owner, repo, branch) -> list[dict]:
    r = await http.get(f"{CFG.api_base}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", headers=hdrs)
    rl = _maybe_rate_limit(r, "tree fetch")
    if rl: raise rl
    if r.status_code != 200:
        raise RuntimeError(f"Tree fetch failed ({r.status_code}): {r.text[:300]}")
    return r.json().get("tree", [])

async def _get_file(http, hdrs, owner, repo, path, branch) -> str | None:
    h = {**hdrs, "Accept": "application/vnd.github.raw+json"}
    try:
        r = await http.get(f"{CFG.api_base}/repos/{owner}/{repo}/contents/{path}?ref={branch}", headers=h)
        rl = _maybe_rate_limit(r, f"content fetch for {path}")
        if rl: raise rl
        return r.text if r.status_code == 200 else None
    except PermissionError:
        raise
    except Exception:
        return None

async def _batch_fetch(http, hdrs, owner, repo, branch, files) -> dict[str, str | None]:
    sem = asyncio.Semaphore(CFG.concurrency)
    async def _one(p):
        async with sem:
            return p, await _get_file(http, hdrs, owner, repo, p, branch)
    results = await asyncio.gather(*[_one(f["path"]) for f in files])
    return {p: c for p, c in results}


# ── file classification ─────────────────────────────────────────────────────

def _classify(tree: list[dict]) -> list[dict]:
    out = []
    for node in tree:
        if node["type"] != "blob":
            continue
        p = node["path"]
        sz = node.get("size", 0)
        parts = PurePosixPath(p).parts
        name = parts[-1] if parts else ""
        ext = PurePosixPath(name).suffix.lower()

        # skip junk
        if any(d.lower() in JUNK_DIRS for d in parts[:-1]): continue
        if ext in JUNK_EXT: continue
        if name.lower() in JUNK_NAMES: continue
        if sz > CFG.big_file: continue
        if name.startswith(".") and name.lower() not in OK_DOTFILES: continue

        tier = _rank(name.lower(), set(d.lower() for d in parts[:-1]))
        out.append({"path": p, "size": sz, "tier": tier})

    out.sort(key=lambda x: (x["tier"], x["size"]))
    return out

def _rank(name: str, dirs: set[str]) -> int:
    if name in KEY_FILES or name.startswith("readme"): return 1
    if dirs & BORING_DIRS: return 3
    return 2


# ── budget-aware selection ──────────────────────────────────────────────────

def _budget_pick(files: list[dict], budget: int) -> list[dict]:
    chosen, est = [], 0
    for f in files:
        if len(chosen) >= CFG.max_fetch: break
        cost = min(f["size"], CFG.file_cap) + 50
        if est + cost > budget * 1.5: break
        chosen.append(f); est += cost
    return chosen


# ── tree rendering ──────────────────────────────────────────────────────────

def _render_tree(paths: list[str], cap: int = 200) -> str:
    d: dict = {}
    for p in sorted(paths):
        node = d
        for part in p.split("/"):
            node = node.setdefault(part, {})

    lines: list[str] = []
    def walk(n, pre=""):
        if len(lines) >= cap:
            lines.append(f"{pre}... (truncated)"); return
        items = sorted(n.items(), key=lambda kv: (bool(kv[1]), kv[0]))
        for i, (k, v) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{pre}{'└── ' if last else '├── '}{k}")
            if v: walk(v, pre + ("    " if last else "│   "))
    walk(d)
    return "\n".join(lines)
