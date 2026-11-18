from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import to_sqlalchemy_url


def make_engine(url: str, connect_timeout: int = 5) -> Engine:
    return create_engine(
        to_sqlalchemy_url(url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": connect_timeout},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
