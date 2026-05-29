from pydantic import BaseModel
from typing import Optional


class SearchResult(BaseModel):
    id: str
    title: str
    type: str
    episodes: int
    status: str
    year: int
    score: float
    poster: str
    session: str


class Episode(BaseModel):
    id: str
    title: str
    episode: float
    season: int
    thumbnail: str
    released: str
    session: str


class MetaResponse(BaseModel):
    id: str
    session: str
    name: str
    description: str
    poster: str
    background: str
    aired: str
    duration: str
    genres: list[str]
    episodes: list[Episode]


class StreamResult(BaseModel):
    title: str
    url: str
    quality: int
    audio: str
    headers: dict[str, str]


class Video(BaseModel):
    title: str
    url: str
    quality: int
    audio: str
