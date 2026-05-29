from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
import re
import asyncio
from typing import Optional
from .models import SearchResult, MetaResponse, StreamResult, Video, Episode
from .kwik import extract_stream_url

BASEURL = "https://animepahe.ru"

# curl_cffi impersonates a real Chrome TLS fingerprint — bypasses DDoS-Guard and Cloudflare
IMPERSONATE = "chrome120"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

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


# ─────────────────────────────────────────────
# GET /search?q=<title>
# ─────────────────────────────────────────────
@app.get("/search", response_model=list[SearchResult], summary="Search for anime by title")
async def search(q: str = Query(..., description="Anime title to search for")):
    """
    Search AnimePahe for an anime by title.
    Returns a list of matches with their session IDs (used for /meta and /streams).
    """
    async with AsyncSession(impersonate=IMPERSONATE) as session:
        resp = await session.get(
            f"{BASEURL}/api",
            params={"m": "search", "l": 8, "q": q},
            headers=HEADERS,
            timeout=15,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AnimePahe returned {resp.status_code}")

    data = resp.json()

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
@app.get("/meta/{session}", response_model=MetaResponse, summary="Get anime metadata + episode list")
async def get_meta(session: str):
    """
    Get full metadata and episode list for an anime.
    `session` is the anime session string from /search (without the 'ap' prefix).
    """
    async with AsyncSession(impersonate=IMPERSONATE) as client:
        # Both requests share the same session so cookies (incl. any challenge cookies)
        # are automatically carried across requests
        page_resp = await client.get(
            f"{BASEURL}/anime/{session}",
            headers=HEADERS,
            timeout=15,
        )

        if page_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Anime not found")

        ep_resp = await client.get(
            f"{BASEURL}/api",
            params={"m": "release", "id": session, "sort": "episode_asc", "page": 1},
            headers=HEADERS,
            timeout=15,
        )

    # Parse HTML metadata
    soup = BeautifulSoup(page_resp.text, "html.parser")

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

    # Parse first page of episodes
    ep_data = ep_resp.json()
    last_page = ep_data.get("last_page", 1)
    all_episodes = list(ep_data.get("data", []))

    # Fetch remaining pages in parallel, reusing the same impersonated session
    # so any cookies from the first request are forwarded automatically
    if last_page > 1:
        async with AsyncSession(impersonate=IMPERSONATE) as client:
            tasks = [
                client.get(
                    f"{BASEURL}/api",
                    params={"m": "release", "id": session, "sort": "episode_asc", "page": p},
                    headers=HEADERS,
                    timeout=15,
                )
                for p in range(2, last_page + 1)
            ]
            responses = await asyncio.gather(*tasks)

        for r in responses:
            page_data = r.json()
            all_episodes.extend(page_data.get("data", []))

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
@app.get("/streams/{anime_session}/{episode_session}", response_model=list[StreamResult], summary="Resolve stream URLs for an episode")
async def get_streams(anime_session: str, episode_session: str):
    """
    Resolve playable HLS stream URLs for a specific episode.
    Both `anime_session` and `episode_session` come from the /meta endpoint.
    Returns streams sorted highest quality first.
    """
    async with AsyncSession(impersonate=IMPERSONATE) as client:
        resp = await client.get(
            f"{BASEURL}/play/{anime_session}/{episode_session}",
            headers=HEADERS,
            timeout=15,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Episode not found")

    soup = BeautifulSoup(resp.text, "html.parser")
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
