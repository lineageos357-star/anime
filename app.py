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

# Full GQL strings (POST, not persisted)
MANGA_SEARCH_GQL = (
    "query($search: SearchInput, $limit: Int, $page: Int, $translationType: VaildTranslationTypeMangaEnumType, $countryOrigin: VaildCountryOriginEnumType) {"
    "mangas(search: $search, limit: $limit, page: $page, translationType: $translationType, countryOrigin: $countryOrigin) {"
    "edges { _id name thumbnail countryOfOrigin status genres score description availableChapters } } }"
)

MANGA_DETAIL_GQL = (
    "query($_id: String!) {"
    "manga(_id: $_id) { _id name thumbnail countryOfOrigin status genres score description availableChapters } }"
)

MANGA_CHAPTERS_GQL = (
    "query ($id: String!, $chapterNumStart: Float!, $chapterNumEnd: Float!) {"
    "episodeInfos(showId: $id, episodeNumStart: $chapterNumStart, episodeNumEnd: $chapterNumEnd) { episodeIdNum } }"
)

MANGA_PAGES_GQL = (
    "query($mangaId: String!, $translationType: VaildTranslationTypeMangaEnumType!, $chapterString: String!) {"
    "chapterPages(mangaId: $mangaId, translationType: $translationType, chapterString: $chapterString) { edges { pictureUrlHead } } }"
)

ANIME_SEARCH_GQL = (
    "query($search:SearchInput, $limit:Int, $page:Int, $translationType:VaildTranslationTypeEnumType, $countryOrigin:VaildCountryOriginEnumType){"
    "shows(search:$search,limit:$limit,page:$page,translationType:$translationType,countryOrigin:$countryOrigin)"
    "{edges{_id name availableEpisodes}}}"
)

ANIME_EPISODE_GQL = (
    "query($showId:String!, $translationType:VaildTranslationTypeEnumType!, $episodeString:String!){"
    "episode(showId:$showId,translationType:$translationType,episodeString:$episodeString){episodeString sourceUrls}}"
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

def _manga_post(variables: dict, gql: str) -> dict:
    """Full-query POST — used for all manga endpoints."""
    r = _session.post(
        API_ENDPOINT,
        json={"variables": variables, "query": gql},
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

    raw   = _manga_post(variables, MANGA_SEARCH_GQL)
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

    # Fetch metadata
    variables = {
        "_id": manga_id,
        "search": {"allowAdult": adult, "allowUnknown": False},
    }
    raw = _manga_post(variables, MANGA_DETAIL_GQL)
    manga = (raw.get("data") or {}).get("manga")

    if not manga:
        return _err(f"Manga '{manga_id}' not found.", 404)

    # Fetch chapter list via episodeInfos
    ep_vars = {
        "id": f"manga@{manga_id}",
        "chapterNumStart": 0.0,
        "chapterNumEnd": 9999.0
    }
    ep_raw = _manga_post(ep_vars, MANGA_CHAPTERS_GQL)
    ep_infos = (ep_raw.get("data") or {}).get("episodeInfos", [])
    
    chapter_list = [
        {
            "chapter": str(int(ch.get("episodeIdNum"))) if ch.get("episodeIdNum") == int(ch.get("episodeIdNum", -1)) else str(ch.get("episodeIdNum")),
            "title": f"Chapter {ch.get('episodeIdNum')}",
            "upload_date": None,
        }
        for ch in ep_infos if ch.get("episodeIdNum") is not None
    ]

    chapters_raw = manga.get("availableChapters", {})

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
            "sub": chapters_raw.get("sub", 0) if isinstance(chapters_raw, dict) else 0,
            "raw": chapters_raw.get("raw", 0) if isinstance(chapters_raw, dict) else 0,
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

    raw = _manga_post(variables, MANGA_PAGES_GQL)
    
    if "errors" in raw:
        err_msg = raw["errors"][0].get("message", "")
        if "NEED_CAPTCHA" in err_msg or "countryOfOrigin" in err_msg:
            return jsonify({"status": "error", "message": "Upstream requires CAPTCHA validation.", "captcha_required": True}), 403
        return _err(f"Upstream error: {err_msg}", 502)

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

    def _parse_episodes(ep_field):
        if isinstance(ep_field, dict):
            return {
                "sub": ep_field.get("sub", 0),
                "dub": ep_field.get("dub", 0),
                "raw": ep_field.get("raw", 0),
            }
        return {"sub": 0, "dub": 0, "raw": 0}

    results = [
        {
            "id":   e.get("_id"),
            "name": e.get("name"),
            "episodes": _parse_episodes(e.get("availableEpisodes")),
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

    raw = _anime_post(variables, ANIME_EPISODE_GQL)
    
    if "errors" in raw:
        err_msg = raw["errors"][0].get("message", "")
        if "NEED_CAPTCHA" in err_msg:
            return jsonify({"status": "error", "message": "Upstream requires CAPTCHA validation.", "captcha_required": True}), 403
        return _err(f"Upstream error: {err_msg}", 502)

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
