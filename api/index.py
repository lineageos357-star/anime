from fastapi import FastAPI, HTTPException, Query, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from bs4 import BeautifulSoup
import cloudscraper
import re
import asyncio
import os
from typing import Optional
from .models import SearchResult, MetaResponse, StreamResult, Video, Episode
from .kwik import extract_stream_url

# ─────────────────────────────────────────────
# cloudscraper handles Cloudflare / DDoS-Guard
# automatically — no manual cookie tricks needed.
# It's sync-only, so we use asyncio.to_thread()
# to run it without blocking FastAPI's event loop.
# ─────────────────────────────────────────────
BASEURL = "https://animepahe.ru"
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

# ─────────────────────────────────────────────
# Auth — API key via X-API-Key header
# Set the API_KEY environment variable in Vercel:
#   vercel env add API_KEY
# Then pass it in every request:
#   curl -H "X-API-Key: yourpassword" https://<deployment>/search?q=...
# ─────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(key: str = Security(api_key_header)):
    secret = os.environ.get("API_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="API_KEY env variable not set on server")
    if key != secret:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key

app = FastAPI(
    title="AnimePahe API",
    description="Unofficial AnimePahe scraper API — search anime, get episode lists, and resolve stream URLs.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def scrape_get(url: str, params: dict = None) -> str:
    """Sync helper — runs in a thread via asyncio.to_thread()."""
    resp = scraper.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.text

def scrape_get_json(url: str, params: dict = None):
    """Sync helper that returns parsed JSON."""
    resp = scraper.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────
# GET /search?q=<title>
# ─────────────────────────────────────────────
@app.get("/search", response_model=list[SearchResult], summary="Search for anime by title", dependencies=[Depends(require_api_key)])
async def search(q: str = Query(..., description="Anime title to search for")):
    """
    Search AnimePahe for an anime by title.
    Returns a list of matches with their session IDs (used for /meta and /streams).
    """
    try:
        data = await asyncio.to_thread(
            scrape_get_json, f"{BASEURL}/api", {"m": "search", "l": 8, "q": q}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AnimePahe request failed: {e}")

    if not data or "data" not in data:
        return []

    return [
        SearchResult(
            id=f"ap{r['session']}",
            title=r["title"],
            type=r.get("type", "TV"),
            episodes=r.get("episodes", 0),
            status=r.get("status", ""),
            year=r.get("year", 0),
            score=r.get("score", 0.0),
            poster=r.get("poster", ""),
            session=r["session"],
        )
        for r in data["data"]
    ]


# ─────────────────────────────────────────────
# GET /meta/{session}
# ─────────────────────────────────────────────
@app.get("/meta/{session}", response_model=MetaResponse, summary="Get anime metadata + episode list", dependencies=[Depends(require_api_key)])
async def get_meta(session: str):
    """
    Get full metadata and episode list for an anime.
    `session` is the anime session string from /search (without the 'ap' prefix).
    """
    try:
        page_html, ep_data = await asyncio.gather(
            asyncio.to_thread(scrape_get, f"{BASEURL}/anime/{session}"),
            asyncio.to_thread(scrape_get_json, f"{BASEURL}/api", {"m": "release", "id": session, "sort": "episode_asc", "page": 1}),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AnimePahe request failed: {e}")

    # Parse HTML metadata
    soup = BeautifulSoup(page_html, "html.parser")

    name_el = soup.select_one('span[style="user-select:text"]')
    name = name_el.get_text(strip=True) if name_el else ""

    desc_el = soup.select_one(".anime-synopsis")
    description = ""
    if desc_el:
        description = desc_el.decode_contents().replace("<br>", "\n").replace("<br/>", "\n").strip()
        description = BeautifulSoup(description, "html.parser").get_text()

    poster_el = soup.select_one('img[data-src$=".jpg"]')
    poster = poster_el["data-src"] if poster_el else ""

    bg_el = soup.select_one("div.anime-cover")
    background = ""
    if bg_el and bg_el.get("data-src"):
        background = "https:" + bg_el["data-src"]

    p_tags = soup.select(".anime-info p")

    def find_tag(label: str):
        for p in p_tags:
            text = p.get_text(separator=" ", strip=True)
            if text.startswith(label):
                return re.sub(r"\s+", " ", text.replace(label, "")).strip()
        return ""

    aired = find_tag("Aired:")
    duration = find_tag("Duration:")
    genres = [g.get_text(strip=True) for g in soup.select(".anime-genre li")]

    # Fetch remaining episode pages in parallel if needed
    last_page = ep_data.get("last_page", 1)
    all_episodes = list(ep_data.get("data", []))

    if last_page > 1:
        extra_pages = await asyncio.gather(*[
            asyncio.to_thread(
                scrape_get_json, f"{BASEURL}/api",
                {"m": "release", "id": session, "sort": "episode_asc", "page": p}
            )
            for p in range(2, last_page + 1)
        ])
        for page in extra_pages:
            all_episodes.extend(page.get("data", []))

    episodes = [
        Episode(
            id=f"ap{session}|{ep['session']}",
            title=ep.get("title") or f"Episode {ep['episode']}",
            episode=ep["episode"],
            season=1,
            thumbnail=ep.get("snapshot", ""),
            released=ep.get("created_at", ""),
            session=ep["session"],
        )
        for ep in all_episodes
    ]
    episodes.sort(key=lambda e: e.episode)

    return MetaResponse(
        id=f"ap{session}",
        session=session,
        name=name,
        description=description,
        poster=poster,
        background=background,
        aired=aired,
        duration=duration,
        genres=genres,
        episodes=episodes,
    )


# ─────────────────────────────────────────────
# GET /streams/{anime_session}/{episode_session}
# ─────────────────────────────────────────────
@app.get("/streams/{anime_session}/{episode_session}", response_model=list[StreamResult], summary="Resolve stream URLs for an episode", dependencies=[Depends(require_api_key)])
async def get_streams(anime_session: str, episode_session: str):
    """
    Resolve playable HLS stream URLs for a specific episode.
    Both `anime_session` and `episode_session` come from the /meta endpoint.
    Returns streams sorted highest quality first.
    """
    try:
        html = await asyncio.to_thread(
            scrape_get, f"{BASEURL}/play/{anime_session}/{episode_session}"
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AnimePahe request failed: {e}")

    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.select("div#resolutionMenu > button")

    if not buttons:
        raise HTTPException(status_code=404, detail="No stream sources found on page")

    async def resolve_button(btn) -> Optional[StreamResult]:
        kwik_url = btn.get("data-src")
        audio = btn.get("data-audio", "jpn")
        quality = btn.get("data-resolution", "?")

        if not kwik_url:
            return None

        try:
            stream_url = await extract_stream_url(kwik_url)
            return StreamResult(
                title=f"{audio} / {quality}p",
                url=stream_url,
                quality=int(quality) if quality.isdigit() else 0,
                audio=audio,
                headers={"Referer": "https://kwik.si"},
            )
        except Exception as e:
            print(f"Failed to extract stream for {quality}p {audio}: {e}")
            return None

    results = await asyncio.gather(*[resolve_button(btn) for btn in buttons])
    streams = [r for r in results if r is not None]
    streams.sort(key=lambda s: s.quality, reverse=True)

    if not streams:
        raise HTTPException(status_code=502, detail="Could not resolve any stream URLs")

    return streams


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "AnimePahe API — visit /docs for interactive docs"}
