"""
AllManga / AllAnime API
Vercel serverless entry point — api/index.py
All routes funnel here via vercel.json rewrites.
"""

import hashlib
import json
import os
import re
import requests
from flask import Flask, jsonify, request
from functools import wraps

app = Flask(__name__)

# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
# Set API_KEY_HASH in your Vercel environment variables.
# Value must be the SHA-256 hex digest of your secret key.
#
# Generate it once:
#   python -c "import hashlib; print(hashlib.sha256(b'your-secret-key').hexdigest())"
#
# Clients must send the SAME hex digest in every request:
#   Header:  X-API-Key: <sha256_of_your_secret_key>
#
# The raw secret never travels over the wire — only its hash.

_API_KEY_HASH = os.environ.get("API_KEY_HASH", "")


def _check_auth() -> bool:
    """Return True if the request carries a valid API key hash."""
    # Auth disabled if env var is not set (local dev convenience).
    if not _API_KEY_HASH:
        return True
    incoming = request.headers.get("X-API-Key", "").strip().lower()
    expected = _API_KEY_HASH.strip().lower()
    # Constant-time compare to prevent timing attacks.
    return (
        len(incoming) == len(expected)
        and hashlib.sha256(incoming.encode()).hexdigest()  # keeps the path length constant
        and incoming == expected
    )


def _auth_required(f):
    """Decorator: reject requests without a valid X-API-Key header."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _check_auth():
            return _err("Unauthorized. Provide a valid X-API-Key header.", 401)
        return f(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

API_ENDPOINT  = "https://api.allanime.day/api"
MANGA_CDN     = "https://ytimgf.fast4speed.rsvp"
ASSET_CDN     = "https://wp.youtube-anime.com/aln.youtube-anime.com"
ALLMANGA_REFR = "https://allmanga.to/"
ALLANIME_REFR = "https://allanime.to/"
IMAGE_REFR    = "https://youtu-chan.com/"

# Persisted Query SHA-256 hashes (manga side)
HASH_SEARCH        = "2d48e19fb67ddcac42fbb885204b6abb0a84f406f15ef83f36de4a66f49f651a"
HASH_MANGA_DETAIL  = "d77781dcf964b97aea0be621dbde430e89e200b58526823ee6010dd11c3ca96a"
HASH_CHAPTER_PAGES = "a062f1b131dae3d17c1950fad14640d066b988ac10347ed49cfbe70f5e7f661b"

# Full GQL strings (anime side — POST, not persisted)
ANIME_SEARCH_GQL = (
    "query($search:SearchInput$limit:Int$page:Int"
    "$translationType:VaildTranslationTypeEnumType"
    "$countryOrigin:VaildCountryOriginEnumType){"
    "shows(search:$search,limit:$limit,page:$page,"
    "translationType:$translationType,countryOrigin:$countryOrigin)"
    "{edges{_id name availableEpisodes{sub dub raw}}}}"
)

ANIME_EPISODE_GQL = (
    "query($showId:String!$translationType:VaildTranslationTypeEnumType!"
    "$episodeString:String!){"
    "episode(showId:$showId,translationType:$translationType,"
    "episodeString:$episodeString){episodeString sourceUrls}}"
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────
# Shared session — persists across warm invocations
# ─────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update({
    "User-Agent": BROWSER_UA,
    "sec-ch-ua-platform": '"Linux"',
})

# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def _manga_get(variables: dict, query_hash: str) -> dict:
    """Persisted Query GET — used for all manga endpoints."""
    params = {
        "variables":  json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": query_hash}},
            separators=(",", ":"),
        ),
    }
    r = _session.get(
        API_ENDPOINT,
        params=params,
        headers={"Referer": ALLMANGA_REFR},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _anime_post(variables: dict, gql: str) -> dict:
    """Full-query POST — used for anime endpoints."""
    r = _session.post(
        API_ENDPOINT,
        json={"variables": variables, "query": gql},
        headers={"Referer": ALLANIME_REFR},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _normalize_cover(raw_path: str | None, width: int = 250) -> str | None:
    """Turn a frame thumbnail path into the primary cover URL on the Asset CDN."""
    if not raw_path:
        return None
    w = width if width in (250, 40) else 250
    normalized = re.sub(r"\d+\.(jpg|png|webp)", r"001.\1", raw_path)
    return f"{ASSET_CDN}/{normalized.lstrip('/')}?w={w}"


def _resolve_page(relative_path: str) -> str:
    return f"{MANGA_CDN}/{relative_path.lstrip('/')}"


def _ok(data, meta: dict | None = None):
    payload = {"status": "ok", "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), 200


def _err(message: str, code: int = 400):
    return jsonify({"status": "error", "message": message}), code


def _require(*param_names):
    """Decorator: return 400 if any listed query-param is absent."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            missing = [p for p in param_names if not request.args.get(p)]
            if missing:
                return _err(f"Missing required parameter(s): {', '.join(missing)}")
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────
# Routes — utility (no auth — safe to expose publicly)
# ─────────────────────────────────────────────────────────

@app.get("/")
def index():
    return _ok({
        "name": "AllManga / AllAnime API",
        "version": "1.0.0",
        "auth": "All data endpoints require X-API-Key header (SHA-256 of your secret).",
        "endpoints": {
            "manga": [
                "GET /manga/search?q=<query>",
                "GET /manga/<id>",
                "GET /manga/<id>/chapter/<chapter>",
            ],
            "anime": [
                "GET /anime/search?q=<query>",
                "GET /anime/<id>/episode/<episode>",
            ],
        },
    })


@app.get("/health")
def health():
    return _ok({"healthy": True})


# ─────────────────────────────────────────────────────────
# Routes — Manga  (all protected)
# ─────────────────────────────────────────────────────────

@app.get("/manga/search")
@_auth_required
@_require("q")
def manga_search():
    """
    Search manga, manhwa, manhua.
    GET /manga/search?q=<query>[&page=1][&limit=26][&adult=false][&country=ALL]
    """
    query   = request.args.get("q")
    page    = max(1, int(request.args.get("page", 1)))
    limit   = min(max(1, int(request.args.get("limit", 26))), 50)
    adult   = request.args.get("adult", "false").lower() == "true"
    country = request.args.get("country", "ALL").upper()

    variables = {
        "search": {
            "query": query,
            "isManga": True,
            "allowAdult": adult,
            "allowUnknown": False,
        },
        "limit": limit,
        "page": page,
        "translationType": "sub",
        "countryOrigin": country,
    }

    raw   = _manga_get(variables, HASH_SEARCH)
    edges = (raw.get("data") or {}).get("mangas", {}).get("edges", [])

    results = [
        {
            "id":          e.get("_id"),
            "name":        e.get("name"),
            "cover":       _normalize_cover(e.get("thumbnail")),
            "thumbnail":   _normalize_cover(e.get("thumbnail"), width=40),
            "country":     e.get("countryOfOrigin"),
            "status":      e.get("status"),
            "genres":      e.get("genres", []),
            "score":       e.get("score"),
            "description": e.get("description"),
        }
        for e in edges
    ]

    return _ok(results, {"page": page, "limit": limit, "total": len(results)})


@app.get("/manga/<manga_id>")
@_auth_required
def manga_detail(manga_id: str):
    """
    Full metadata + chapter list for a series.
    GET /manga/<id>[?adult=false]
    """
    adult = request.args.get("adult", "false").lower() == "true"

    variables = {
        "_id": manga_id,
        "search": {"allowAdult": adult, "allowUnknown": False},
    }

    raw   = _manga_get(variables, HASH_MANGA_DETAIL)
    manga = (raw.get("data") or {}).get("manga")

    if not manga:
        return _err(f"Manga '{manga_id}' not found.", 404)

    chapters_raw = manga.get("availableChapters", {})
    chapter_list = [
        {
            "chapter":     ch.get("chapterString"),
            "title":       ch.get("title"),
            "upload_date": ch.get("uploadDate"),
        }
        for ch in manga.get("chapters", [])
    ]

    return _ok({
        "id":          manga.get("_id"),
        "name":        manga.get("name"),
        "cover":       _normalize_cover(manga.get("thumbnail")),
        "country":     manga.get("countryOfOrigin"),
        "status":      manga.get("status"),
        "genres":      manga.get("genres", []),
        "score":       manga.get("score"),
        "description": manga.get("description"),
        "chapters": {
            "sub": chapters_raw.get("sub", 0),
            "raw": chapters_raw.get("raw", 0),
        },
        "chapter_list": chapter_list,
    })


@app.get("/manga/<manga_id>/chapter/<chapter_string>")
@_auth_required
def manga_chapter(manga_id: str, chapter_string: str):
    """
    Page image URLs for a specific chapter.
    GET /manga/<id>/chapter/<ch>[?lang=sub][&limit=100][&offset=0]
    """
    lang   = request.args.get("lang", "sub")
    limit  = min(max(1, int(request.args.get("limit", 100))), 200)
    offset = max(0, int(request.args.get("offset", 0)))

    variables = {
        "mangaId":         manga_id,
        "translationType": lang,
        "chapterString":   chapter_string,
        "limit":           limit,
        "offset":          offset,
    }

    raw       = _manga_get(variables, HASH_CHAPTER_PAGES)
    pages_raw = (raw.get("data") or {}).get("chapterPages", {})

    if not pages_raw:
        return _err(
            f"Chapter '{chapter_string}' not found for '{manga_id}'.", 404
        )

    edges = pages_raw.get("edges", [])
    pages = [
        {
            "page":    i + 1,
            "url":     _resolve_page(
                edge.get("pictureUrlHead") or edge.get("url") or ""
            ),
            "referer": IMAGE_REFR,
        }
        for i, edge in enumerate(edges)
    ]

    return _ok(pages, {
        "manga_id": manga_id,
        "chapter":  chapter_string,
        "lang":     lang,
        "total":    len(pages),
        "note":     f"Send 'Referer: {IMAGE_REFR}' when fetching image URLs.",
    })


# ─────────────────────────────────────────────────────────
# Routes — Anime  (all protected)
# ─────────────────────────────────────────────────────────

@app.get("/anime/search")
@_auth_required
@_require("q")
def anime_search():
    """
    Search anime series.
    GET /anime/search?q=<query>[&page=1][&limit=40][&mode=sub|dub|raw][&country=ALL]
    """
    query   = request.args.get("q")
    page    = max(1, int(request.args.get("page", 1)))
    limit   = min(max(1, int(request.args.get("limit", 40))), 50)
    mode    = request.args.get("mode", "sub")
    country = request.args.get("country", "ALL").upper()

    variables = {
        "search": {
            "allowAdult": False,
            "allowUnknown": False,
            "query": query,
        },
        "limit": limit,
        "page": page,
        "translationType": mode,
        "countryOrigin": country,
    }

    raw   = _anime_post(variables, ANIME_SEARCH_GQL)
    edges = (raw.get("data") or {}).get("shows", {}).get("edges", [])

    results = [
        {
            "id":   e.get("_id"),
            "name": e.get("name"),
            "episodes": {
                "sub": e.get("availableEpisodes", {}).get("sub", 0),
                "dub": e.get("availableEpisodes", {}).get("dub", 0),
                "raw": e.get("availableEpisodes", {}).get("raw", 0),
            },
        }
        for e in edges
    ]

    return _ok(results, {"page": page, "limit": limit, "total": len(results)})


@app.get("/anime/<show_id>/episode/<episode_string>")
@_auth_required
def anime_episode_sources(show_id: str, episode_string: str):
    """
    Stream source URLs for a specific episode.
    GET /anime/<id>/episode/<ep>[?mode=sub|dub|raw]
    """
    mode = request.args.get("mode", "sub")

    variables = {
        "showId":          show_id,
        "translationType": mode,
        "episodeString":   episode_string,
    }

    raw     = _anime_post(variables, ANIME_EPISODE_GQL)
    episode = (raw.get("data") or {}).get("episode")

    if not episode:
        return _err(
            f"Episode '{episode_string}' not found for show '{show_id}'.", 404
        )

    sources = sorted(
        [
            {
                "source_name": s.get("sourceName"),
                "url":         s.get("url"),
                "type":        s.get("type"),
                "priority":    s.get("priority"),
            }
            for s in episode.get("sourceUrls", [])
        ],
        key=lambda x: x.get("priority") or 0,
        reverse=True,
    )

    return _ok({
        "show_id": show_id,
        "episode": episode_string,
        "mode":    mode,
        "sources": sources,
    })


# ─────────────────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(_):
    return _err("Endpoint not found.", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return _err("Method not allowed. All endpoints are GET.", 405)


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled: {e}")
    return _err(f"Upstream error: {e}", 502)


# ─────────────────────────────────────────────────────────
# Local dev entrypoint (ignored by Vercel)
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)
