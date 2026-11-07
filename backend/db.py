from contextlib import contextmanager

import pymysql
import pymysql.cursors

import config


def connect():
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@contextmanager
def tx():
    """One database transaction. Commits on success, rolls back on any exception."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql, params=None, one=False):
    with tx() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone() if one else cur.fetchall()
