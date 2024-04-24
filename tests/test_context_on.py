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
    #   ctx.run(ctx.on.install, state)
    with ctx.manager(getattr(ctx.on, event_name), scenario.State()) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        assert isinstance(mgr.charm.observed[0], event_kind)


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("secret_changed", ops.SecretChangedEvent),
        ("secret_rotate", ops.SecretRotateEvent),
    ],
)
def test_simple_secret_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    secret = scenario.Secret("secret:123", {0: {"password": "xxxx"}}, owner=None)
    state_in = scenario.State(secrets=[secret])
    # These look like:
    #   ctx.run(ctx.on.secret_changed(secret=secret), state)
    # The secret must always be passed because the same event name is used for
    # all secrets.
    with ctx.manager(getattr(ctx.on, event_name)(secret=secret), state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
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
        "secret:123", {42: {"password": "yyyy"}, 43: {"password": "xxxx"}}, owner=None
    )
    state_in = scenario.State(secrets=[secret])
    # These look like:
    #   ctx.run(ctx.on.secret_expired(secret=secret, revision=revision), state)
    # The secret and revision must always be passed because the same event name
    # is used for all secrets.
    with ctx.manager(
        getattr(ctx.on, event_name)(secret=secret, revision=42), state_in
    ) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.secret.id == secret.id
        assert event.revision == 42


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("foo_storage_attached", ops.StorageAttachedEvent),
        ("foo_storage_detaching", ops.StorageDetachingEvent),
    ],
)
def test_storage_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    storage = scenario.Storage("foo")
    state_in = scenario.State(storage=[storage])
    # These look like:
    #   ctx.run(ctx.on.foo_storage_attached, state)
    # The storage is inferred from the event name.
    with ctx.manager(getattr(ctx.on, event_name), state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.storage.name == storage.name
        assert event.storage.index == storage.index


def test_action_event_no_params():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    # These look like:
    #   ctx.run_action(ctx.on.act_action, state)
    # The action is inferred from the event name.
    with ctx.action_manager(ctx.on.act_action, scenario.State()) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, ops.ActionEvent)


def test_action_event_with_params():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    action = scenario.Action("act", {"param": "hello"})
    # These look like:
    #   ctx.run_action(ctx.on.act_action(action=action), state)
    # So that any parameters can be included and the ID can be customised.
    with ctx.action_manager(ctx.on.act_action(action=action), scenario.State()) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, ops.ActionEvent)
        assert event.id == action.id
        assert event.params["param"] == action.params["param"]


def test_pebble_ready_event():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    container = scenario.Container("bar", can_connect=True)
    state_in = scenario.State(containers=[container])
    # These look like:
    #   ctx.run(ctx.on.bar_pebble_ready, state)
    # The container/workload is inferred from the event name.
    with ctx.manager(ctx.on.bar_pebble_ready, state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, ops.PebbleReadyEvent)
        assert event.workload.name == container.name


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("baz_relation_created", ops.RelationCreatedEvent),
        ("baz_relation_broken", ops.RelationBrokenEvent),
    ],
)
def test_relation_app_events(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz")
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_created, state)
    # The relation is inferred from the event name.
    with ctx.manager(getattr(ctx.on, event_name), state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.relation_id
        assert event.app.name == relation.remote_app_name
        assert event.unit is None


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("baz_relation_joined", ops.RelationJoinedEvent),
        ("baz_relation_changed", ops.RelationChangedEvent),
    ],
)
def test_relation_unit_events_default_unit(event_name, event_kind):
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz", remote_units_data={1: {"x": "y"}})
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_changed, state)
    # The relation is inferred from the event name and the unit is chosen
    # automatically.
    with ctx.manager(getattr(ctx.on, event_name), state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.relation_id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/1"


@pytest.mark.parametrize(
    "event_name, event_kind",
    [
        ("baz_relation_joined", ops.RelationJoinedEvent),
        ("baz_relation_changed", ops.RelationChangedEvent),
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
    # The relation is inferred from the event name, and an explicit unit choice is provided.
    with ctx.manager(getattr(ctx.on, event_name)(unit=2), state_in) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, event_kind)
        assert event.relation.id == relation.relation_id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/2"


def test_relation_departed_event():
    ctx = scenario.Context(ContextCharm, meta=META, actions=ACTIONS)
    relation = scenario.Relation("baz")
    state_in = scenario.State(relations=[relation])
    # These look like:
    #   ctx.run(ctx.on.baz_relation_departed(unit=unit_ordinal, departing_unit=unit_ordinal), state)
    # The relation is inferred from the event name, and an explicit unit choice is provided for
    # both the triggering unit and the departing unit.
    with ctx.manager(
        ctx.on.baz_relation_departed(unit=2, departing_unit=1), state_in
    ) as mgr:
        mgr.run()
        assert len(mgr.charm.observed) == 2
        assert isinstance(mgr.charm.observed[1], ops.CollectStatusEvent)
        event = mgr.charm.observed[0]
        assert isinstance(event, ops.RelationDepartedEvent)
        assert event.relation.id == relation.relation_id
        assert event.app.name == relation.remote_app_name
        assert event.unit.name == "remote/2"
        assert event.departing_unit.name == "remote/1"
