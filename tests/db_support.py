"""Disposable local databases for cross-dialect regression tests.

MM_TEST_POSTGRES_URL must point to a loopback database named mm_test_*.
DATABASE_URL from the caller is never trusted for destructive tests.
"""
import os
import uuid
from sqlalchemy import create_engine as sa_create_engine, text
from sqlalchemy.engine import make_url

_ENGINES = []


def postgres_url():
    raw = os.environ.get('MM_TEST_POSTGRES_URL')
    if not raw:
        return None
    url = make_url(raw)
    if (url.get_backend_name() != 'postgresql' or url.host not in {'127.0.0.1', 'localhost', '::1'}
            or not (url.database or '').startswith('mm_test_') or url.query):
        raise RuntimeError('Tests require a loopback PostgreSQL mm_test_* database without URL options')
    return url.set(drivername='postgresql+psycopg2')


def create_test_engine(sqlite_url='sqlite:///:memory:', **kwargs):
    url = postgres_url()
    if url is None:
        return sa_create_engine(sqlite_url, **kwargs)
    schema = 'mm_test_' + uuid.uuid4().hex
    control = sa_create_engine(url)
    with control.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = sa_create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    _ENGINES.append((engine, control, schema))
    return engine


def cleanup_engines():
    for engine, control, schema in reversed(_ENGINES):
        engine.dispose()
        with control.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
    _ENGINES.clear()
