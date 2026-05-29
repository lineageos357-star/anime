"""
kwik.py — Extracts the HLS stream URL from a Kwik embed page.

Uses curl_cffi to impersonate Chrome and bypass Cloudflare protection on kwik.si.

Steps:
  1. Fetch the Kwik embed page (with Referer: animepahe.com, Chrome impersonation)
  2. Find the <script> tag containing eval(function(...)
  3. Unpack the P,A,C,K,E,D obfuscated JS
  4. Extract `const source='<url>'` from the unpacked output
"""

import re
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup


# Kwik sits behind Cloudflare — chrome120 impersonation bypasses the JS challenge
IMPERSONATE = "chrome120"

HEADERS = {
    "Referer": "https://animepahe.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


async def extract_stream_url(kwik_url: str) -> str:
    """
    Given a Kwik embed URL (data-src from AnimePahe's resolution buttons),
    returns the direct HLS .m3u8 stream URL.
    """
    async with AsyncSession(impersonate=IMPERSONATE) as session:
        resp = await session.get(kwik_url, headers=HEADERS, timeout=15)

    if resp.status_code != 200:
        raise ValueError(f"Kwik returned HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    scripts = soup.find_all("script")

    # Find the script containing the packed JS
    packed_script = None
    for script in scripts:
        if script.string and "eval(function(" in script.string:
            packed_script = script.string
            break

    if not packed_script:
        raise ValueError(
            f"Could not find packed script on Kwik page. "
            f"Status: {resp.status_code}. "
            f"Page preview: {resp.text[:200]}"
        )

    # Extract everything from eval(function( onwards
    eval_start = packed_script.index("eval(function(")
    packed_js = packed_script[eval_start:]

    # Unpack the obfuscated JS
    unpacked = unpack_js(packed_js)

    # Extract the source URL: const source='<url>';
    match = re.search(r"const source='([^']+)'", unpacked)
    if not match:
        # Fallback: any m3u8 URL in the unpacked output
        match = re.search(r'["\']([^"\']+\.m3u8[^"\']*)["\']', unpacked)

    if not match:
        raise ValueError(
            f"Could not find source URL in unpacked JS.\n"
            f"Unpacked preview: {unpacked[:400]}"
        )

    return match.group(1)


def unpack_js(packed: str) -> str:
    """
    Pure Python implementation of the P,A,C,K,E,D JS unpacker.

    The packed format is:
      eval(function(p,a,c,k,e,d){ ... }('PAYLOAD',BASE,COUNT,'DICT|...'.split('|'),0,{}))

    Steps:
      1. Extract PAYLOAD, BASE, COUNT, DICT from the outer call
      2. Replace each base-N encoded token in PAYLOAD with the word from DICT
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
        """Convert a base-N string (0-9a-z alphabet) to a decimal int."""
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
