import asyncpg
from app.backend.config import settings
import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

async def get_connection(database=None):
    return await asyncpg.connect(
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        database=database or settings.DB_NAME
    )

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.backend.config import settings

DATABASE_URL = (
    f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@"
    f"{settings.DB_HOST}/{settings.DB_NAME}"
)

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

async def get_connection_for_authentication():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def fetch_record_by_id(record_id: int):
    conn = await get_connection()
    try:
        query = f'SELECT * FROM public.{DB_TABLE_NAME} WHERE record_id = $1'
        row = await conn.fetchrow(query, record_id)
        await conn.close()
        return dict(row) if row else None
    finally:
        await conn.close()