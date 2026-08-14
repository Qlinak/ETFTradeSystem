"""SQLAlchemy engine and session management."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import get_database_settings


settings = get_database_settings()

engine = create_engine(
    settings.sqlalchemy_url,
    echo=settings.echo_sql,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db_session() -> Generator[Session, None, None]:
    """Yield a transactional SQLAlchemy session for request handling."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()