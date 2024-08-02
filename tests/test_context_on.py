import copy

import ops
import pytest

import scenario

META = {
    "name": "context-charm",
    "containers": {
        "bar": {},
    },
    "requires": {
        "baz": {
            "interface": "charmlink",
        }
    },
    "storage": {
        "foo": {
            "type": "filesystem",
        }
    },
}
ACTIONS = {
    "act": {
        "params": {
            "param": {
                "description": "some parameter",
                "type": "string",
                "default": "",
            }
        }
    },
}


class ContextCharm(ops.CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.observed = []
        for event in self.on.events().values():
            framework.observe(event, self._on_event)

    def _on_event(self, event):
        self.observed.append(event)


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("install", ops.InstallEvent),
        ("start", ops.StartEvent),
        ("stop", ops.StopEvent),
        ("remove", ops.RemoveEvent),
        ("update_status", ops.UpdateStatusEvent),
        ("config_changed", ops.ConfigChangedEvent),
        ("upgrade_charm", ops.UpgradeCharmEvent),
        ("pre_series_upgrade", ops.PreSeriesUpgradeEvent),
        ("post_series_upgrade", ops.PostSeriesUpgradeEvent),
        ("leader_elected", ops.LeaderElectedEvent),
    ],
)
def test_simple_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    # These look like:
    #   ctx.run(ctx.on.install(), state)
    with ctx(getattr(ctx.on, event_name)(), scenario.State()) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        assert isinstance(event.charm.observed[0], event_kind)


@pytest.mark.parametrize("as_kwarg", [True, False])
@pytest.mark.parametrize(
    "event_name,event_kind,owner",
    [
        ("secret_changed", ops.SecretChangedEvent, None),
        ("secret_rotate", ops.SecretRotateEvent, "app"),
    ],
)
def test_simple_secret_events(as_kwarg, event_name, event_kind, owner):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    secret = scenario.Secret(
        id="secret:123", contents={0: {"password": "xxxx"}}, owner=owner
    )
    state_in = scenario.State(secrets=[secret])
    # These look like:
    #   ctx.run(ctx.on.secret_changed(secret=secret), state)
    # The secret must always be passed because the same event name is used for
    # all secrets.
    if as_kwarg:
        args = ()
        kwargs = {"secret": secret}
    else:
        args = (secret,)
        kwargs = {}
    with ctx(getattr(ctx.on, event_name)(*args, **kwargs), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.secret.id == secret.id


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("secret_expired", ops.SecretExpiredEvent),
        ("secret_remove", ops.SecretRemoveEvent),
    ],
)
def test_revision_secret_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    secret = scenario.Secret(
        id="secret:123",
        contents={42: {"password": "yyyy"}, 43: {"password": "xxxx"}},
        owner="app",
    )
    state_in = scenario.State(secrets=[secret])
    # These look like:
    #   ctx.run(ctx.on.secret_expired(secret=secret, revision=revision), state)
    # The secret and revision must always be passed because the same event name
    # is used for all secrets.
    with ctx(getattr(ctx.on, event_name)(secret, revision=42), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.secret.id == secret.id
        assert event.revision == 42


@pytest.mark.parametrize("event_name", ["secret_expired", "secret_remove"])
def test_revision_secret_events_as_positional_arg(event_name):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    secret = scenario.Secret(
        id="secret:123",
        contents={42: {"password": "yyyy"}, 43: {"password": "xxxx"}},
        owner=None,
    )
    state_in = scenario.State(secrets=[secret])
    with pytest.raises(TypeError):
        ctx.run(getattr(ctx.on, event_name)(secret, 42), state_in)


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("storage_attached", ops.StorageAttachedEvent),
        ("storage_detaching", ops.StorageDetachingEvent),
    ],
)
def test_storage_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    storage = scenario.Storage("foo")
    state_in = scenario.State(storages=[storage])
    # These look like:
    #   ctx.run(ctx.on.storage_attached(storage), state)
    with ctx(getattr(ctx.on, event_name)(storage), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.storage.name == storage.name
        assert event.storage.index == storage.index


def test_action_event_no_params():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    # These look like:
    #   ctx.run(ctx.on.action(action_name), state)
    with ctx(ctx.on.action("act"), scenario.State()) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, ops.ActionEvent)


def test_action_event_with_params():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    # These look like:
    #   ctx.run(ctx.on.action(action=action), state)
    # So that any parameters can be included and the ID can be customised.
    call_event = ctx.on.action("act", params={"param": "hello"})
    with ctx(call_event, scenario.State()) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, ops.ActionEvent)
        assert event.id == call_event.action.id
        assert event.params["param"] == call_event.action.params["param"]


def test_pebble_ready_event():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    container = scenario.Container("bar", can_connect=True)
    state_in = scenario.State(containers=[container])
    # These look like:
    #   ctx.run(ctx.on.pebble_ready(container), state)
    with ctx(ctx.on.pebble_ready(container), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, ops.PebbleReadyEvent)
        assert event.workload.name == container.name


@pytest.mark.parametrize("as_kwarg", [True, False])
@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("relation_created", ops.RelationCreatedEvent),
        ("relation_broken", ops.RelationBrokenEvent),
    ],
)
def test_relation_app_events(as_kwarg, event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz")
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.relation_created(relation), state)
    if as_kwarg:
        args = ()
        kwargs = {"relation": relation}
    else:
        args = (relation,)
        kwargs = {}
    with ctx(getattr(ctx.on, event_name)(*args, **kwargs), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.id
        assert event.app.name == relation.remote_app_name
        assert event.unit is None


def test_relation_complex_name():
    meta = copy.deepcopy(META)
    meta["requires"]["foo-bar-baz"] = {"interface": "another-one"}
    ctx = scenario.Context(ContextCharm, meta=meta, actions=ACTIONS)
    relation = scenario.Relation("foo-bar-baz")
    state_in = scenario.State(relations=[relation])
    with ctx(ctx.on.relation_created(relation), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        event = event.charm.observed[0]
        assert isinstance(event, ops.RelationCreatedEvent)
        assert event.relation.id == relation.id
        assert event.app.name == relation.remote_app_name
        assert event.unit is None


@pytest.mark.parametrize("event_name", ["relation_created", "relation_broken"])
def test_relation_events_as_positional_arg(event_name):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz")
    state_in = scenario.State(relations=[relation])
    with pytest.raises(TypeError):
        ctx.run(getattr(ctx.on, event_name)(relation, 0), state_in)


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("relation_joined", ops.RelationJoinedEvent),
        ("relation_changed", ops.RelationChangedEvent),
    ],
)
def test_relation_unit_events_default_unit(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz", remote_units_data={1: {"x": "y"}})
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_changed, state)
    # The unit is chosen automatically.
    with ctx(getattr(ctx.on, event_name)(relation), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/1"


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("relation_joined", ops.RelationJoinedEvent),
        ("relation_changed", ops.RelationChangedEvent),
    ],
)
def test_relation_unit_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation(
        "baz", remote_units_data={1: {"x": "y"}, 2: {"x": "z"}}
    )
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_changed(unit=unit_ordinal), state)
    with ctx(getattr(ctx.on, event_name)(relation, remote_unit=2), state_in) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/2"


def test_relation_departed_event():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz")
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_departed(unit=unit_ordinal, departing_unit=unit_ordinal), state)
    with ctx(
        ctx.on.relation_departed(relation, remote_unit=2, departing_unit=1), state_in
    ) as event:
        event.run()
        assert len(event.charm.observed) == 2
        assert isinstance(event.charm.observed[1], ops.CollectStatusEvent)
        event = event.charm.observed[0]
        assert isinstance(event, ops.RelationDepartedEvent)
        assert event.relation.id == relation.id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/2"
        assert event.departing_unit.name == "remote/1"