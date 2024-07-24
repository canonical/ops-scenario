import pytest
from ops import RelationNotFoundError
from ops.charm import CharmBase
from ops.framework import Framework

from scenario import Context
from scenario.state import Network, Relation, State, SubordinateRelation


@pytest.fixture(scope="function")
def mycharm():
    class MyCharm(CharmBase):
        _call = None
        called = False

        def __init__(self, framework: Framework):
            super().__init__(framework)

            for evt in self.on.events().values():
                self.framework.observe(evt, self._on_event)

        def _on_event(self, event):
            if MyCharm._call:
                MyCharm.called = True
                MyCharm._call(self, event)

    return MyCharm


def test_ip_get(mycharm):
    ctx = Context(
        mycharm,
        meta={
            "name": "foo",
            "requires": {
                "metrics-endpoint": {"interface": "foo"},
                "deadnodead": {"interface": "bar"},
            },
            "extra-bindings": {"foo": {}},
        },
    )

    with ctx(
        ctx.on.update_status(),
        State(
            relations=[
                Relation(
                    interface="foo",
                    remote_app_name="remote",
                    endpoint="metrics-endpoint",
                    id=1,
                ),
            ],
            networks={Network.default("foo", private_address="4.4.4.4")},
        ),
    ) as event:
        # we have a network for the relation
        rel = event.charm.model.get_relation("metrics-endpoint")
        assert (
            str(event.charm.model.get_binding(rel).network.bind_address) == "192.0.2.0"
        )

        # we have a network for a binding without relations on it
        assert (
            str(event.charm.model.get_binding("deadnodead").network.bind_address)
            == "192.0.2.0"
        )

        # and an extra binding
        assert (
            str(event.charm.model.get_binding("foo").network.bind_address) == "4.4.4.4"
        )


def test_no_sub_binding(mycharm):
    ctx = Context(
        mycharm,
        meta={
            "name": "foo",
            "requires": {"bar": {"interface": "foo", "scope": "container"}},
        },
    )

    with ctx(
        ctx.on.update_status(),
        State(
            relations=[
                SubordinateRelation("bar"),
            ]
        ),
    ) as event:
        with pytest.raises(RelationNotFoundError):
            # sub relations have no network
            event.charm.model.get_binding("bar").network


def test_no_relation_error(mycharm):
    """Attempting to call get_binding on a non-existing relation -> RelationNotFoundError"""

    ctx = Context(
        mycharm,
        meta={
            "name": "foo",
            "requires": {"metrics-endpoint": {"interface": "foo"}},
            "extra-bindings": {"bar": {}},
        },
    )

    with ctx(
        ctx.on.update_status(),
        State(
            relations=[
                Relation(
                    interface="foo",
                    remote_app_name="remote",
                    endpoint="metrics-endpoint",
                    id=1,
                ),
            ],
            networks={Network.default("bar")},
        ),
    ) as event:
        with pytest.raises(RelationNotFoundError):
            net = event.charm.model.get_binding("foo").network
