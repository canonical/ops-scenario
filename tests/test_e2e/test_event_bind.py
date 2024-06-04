import pytest

from scenario import Container, Relation, Secret, State


def test_bind_relation():
    event = _Event("foo-relation-changed")
    foo_relation = Relation("foo")
    state = State(relations=[foo_relation])
    assert event.bind(state).relation is foo_relation


def test_bind_relation_complex_name():
    event = _Event("foo-bar-baz-relation-changed")
    foo_relation = Relation("foo_bar_baz")
    state = State(relations=[foo_relation])
    assert event.bind(state).relation is foo_relation


def test_bind_relation_notfound():
    event = _Event("foo-relation-changed")
    state = State(relations=[])
    with pytest.raises(BindFailedError):
        event.bind(state)


def test_bind_relation_toomany(caplog):
    event = _Event("foo-relation-changed")
    foo_relation = Relation("foo")
    foo_relation1 = Relation("foo")
    state = State(relations=[foo_relation, foo_relation1])
    event.bind(state)
    assert "too many relations" in caplog.text


def test_bind_secret():
    event = _Event("secret-changed")
    secret = Secret("foo", {"a": "b"})
    state = State(secrets=[secret])
    assert event.bind(state).secret is secret


def test_bind_secret_notfound():
    event = _Event("secret-changed")
    state = State(secrets=[])
    with pytest.raises(BindFailedError):
        event.bind(state)


def test_bind_container():
    event = _Event("foo-pebble-ready")
    container = Container("foo")
    state = State(containers=[container])
    assert event.bind(state).container is container


def test_bind_container_notfound():
    event = _Event("foo-pebble-ready")
    state = State(containers=[])
    with pytest.raises(BindFailedError):
        event.bind(state)
