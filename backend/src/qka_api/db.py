from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from qka_shared.config import Settings


def create_engine_from_settings(settings: Settings):
    connect_args: dict = {}
    if settings.database_read_only:
        connect_args["options"] = "-c default_transaction_read_only=on"
    return create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)


def create_session_factory(settings: Settings):
    engine = create_engine_from_settings(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return engine, factory


def session_dependency(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
