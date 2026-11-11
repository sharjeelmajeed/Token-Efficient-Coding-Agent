"""The store reuses one connection per thread without weakening transactions."""

import sqlite3
import threading

import pytest

from token_agent.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(str(tmp_path / "state.sqlite3"))
    yield store
    store.close()


def test_one_connection_is_reused_on_a_thread(store):
    with store.connection() as first:
        pass
    with store.connection(True) as second:
        pass
    assert first is second


def test_threads_never_share_a_connection(store):
    with store.connection() as mine:
        pass
    seen = []

    def other():
        with store.connection() as db:
            seen.append(db)
        store.close()

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert seen and seen[0] is not mine


def test_nested_use_gets_its_own_connection_and_both_commit(store):
    with store.connection(True) as outer:
        outer.execute("INSERT INTO accounts VALUES ('outer', 1)")
        with store.connection() as inner:
            assert inner is not outer
            inner.execute("SELECT 1").fetchone()
    with store.connection() as db:
        assert db.execute("SELECT budget FROM accounts WHERE owner='outer'").fetchone()[0] == 1


def test_a_failed_transaction_rolls_back_and_the_store_keeps_working(store):
    with pytest.raises(RuntimeError):
        with store.connection(True) as db:
            db.execute("INSERT INTO accounts VALUES ('lost', 1)")
            raise RuntimeError("fail mid-transaction")
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM accounts WHERE owner='lost'").fetchone()[0] == 0
        assert not db.in_transaction


def test_writes_are_visible_to_a_separate_connection_at_once(store):
    """Durability is unchanged: every write is committed before the block returns."""
    with store.connection(True) as db:
        db.execute("INSERT INTO accounts VALUES ('seen', 7)")
    other = sqlite3.connect(store.path)
    try:
        assert other.execute("SELECT budget FROM accounts WHERE owner='seen'").fetchone()[0] == 7
    finally:
        other.close()


def test_close_releases_the_connection(store):
    with store.connection() as db:
        pass
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    with store.connection() as fresh:
        assert fresh is not db
