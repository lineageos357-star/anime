"""
kwik.py — Extracts the HLS stream URL from a Kwik embed page.

Uses cloudscraper to bypass Cloudflare protection on kwik.si.

Steps:
  1. Fetch the Kwik embed page (cloudscraper handles the CF challenge)
  2. Find the <script> tag containing eval(function(...)
  3. Unpack the P,A,C,K,E,D obfuscated JS
  4. Extract `const source='<url>'` from the unpacked output
"""

import re
import asyncio
import cloudscraper
from bs4 import BeautifulSoup

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

HEADERS = {
    "Referer": "https://animepahe.com",
}


def _fetch_kwik(kwik_url: str) -> str:
    """Sync fetch — called via asyncio.to_thread()."""
    resp = scraper.get(kwik_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


async def extract_stream_url(kwik_url: str) -> str:
    """
    Given a Kwik embed URL (data-src from AnimePahe's resolution buttons),
    returns the direct HLS .m3u8 stream URL.
    """
    html = await asyncio.to_thread(_fetch_kwik, kwik_url)

    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")

    packed_script = None
    for script in scripts:
        if script.string and "eval(function(" in script.string:
            packed_script = script.string
            break

    if not packed_script:
        raise ValueError(
            f"Could not find packed script on Kwik page. "
            f"Page preview: {html[:300]}"
        )

    eval_start = packed_script.index("eval(function(")
    packed_js = packed_script[eval_start:]

    unpacked = unpack_js(packed_js)

    match = re.search(r"const source='([^']+)'", unpacked)
    if not match:
        match = re.search(r'["\']([^"\']+\.m3u8[^"\']*)["\']', unpacked)

    if not match:
        raise ValueError(
            f"Could not find source URL in unpacked JS.\n"
            f"Unpacked preview: {unpacked[:400]}"
        )

    return match.group(1)


def unpack_js(packed: str) -> str:
    """
    Pure Python P,A,C,K,E,D unpacker.
    Decodes obfuscated JS to extract the raw stream URL.
    """
    match = re.search(
        r"eval\(function\(p,a,c,k,e,(?:d|r)\)\{.+?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)",
        packed,
        re.DOTALL,
    )

    if not match:
        raise ValueError("Input does not match expected P,A,C,K,E,D format")

    payload = match.group(1)
    base = int(match.group(2))
    raw_dict = match.group(4)
    dictionary = raw_dict.split("|")

    def base_n_to_int(s: str, base: int) -> int:
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        result = 0
        for ch in s:
            result = result * base + chars.index(ch)
        return result

    def replace_token(m: re.Match) -> str:
        token = m.group(0)
        index = base_n_to_int(token, base)
        word = dictionary[index] if index < len(dictionary) else token
        return word if word else token

    return re.sub(r"\w+", replace_token, payload)
