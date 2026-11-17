"""Reference implementation used only to validate the URL-shortener acceptance suite."""

from __future__ import annotations

import os
import re
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

engine = create_engine(
    os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
)
app = FastAPI()


class Base(DeclarativeBase):
    pass


class Link(Base):
    __tablename__ = "links"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    clicks: Mapped[int] = mapped_column(Integer, default=0)


Base.metadata.create_all(engine)


class LinkIn(BaseModel):
    url: str
    code: str | None = None

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("expected an absolute HTTP URL")
        return value

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z0-9_-]{4,20}", value) is None:
            raise ValueError("invalid code")
        return value


@app.exception_handler(RequestValidationError)
async def invalid_input(_request: Request, _error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "invalid input"})


@app.exception_handler(HTTPException)
async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"error": error.detail})


def data(link: Link) -> dict:
    return {"code": link.code, "url": link.url, "clicks": link.clicks}


def get_link(db: Session, code: str) -> Link:
    link = db.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="unknown code")
    return link


@app.post("/links", status_code=201)
def create(payload: LinkIn) -> dict:
    with Session(engine) as db:
        code = payload.code or uuid.uuid4().hex[:12]
        if db.scalar(select(Link).where(Link.code == code)) is not None:
            raise HTTPException(status_code=409, detail="duplicate code")
        link = Link(code=code, url=payload.url, clicks=0)
        db.add(link)
        db.commit()
        return data(link)


@app.get("/links")
def list_links() -> dict:
    with Session(engine) as db:
        items = [data(link) for link in db.scalars(select(Link).order_by(Link.id))]
        return {"items": items, "total": len(items)}


@app.get("/links/{code}")
def show(code: str) -> dict:
    with Session(engine) as db:
        return data(get_link(db, code))


@app.get("/r/{code}")
def redirect(code: str) -> RedirectResponse:
    with Session(engine) as db:
        link = get_link(db, code)
        link.clicks += 1
        url = link.url
        db.commit()
        return RedirectResponse(url, status_code=302)
