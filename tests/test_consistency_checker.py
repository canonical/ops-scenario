import dataclasses

import pytest
from ops.charm import CharmBase

from scenario.consistency_checker import check_consistency
from scenario.runtime import InconsistentScenarioError
from scenario.state import (
    RELATION_EVENTS_SUFFIX,
    Action,
    Container,
    Network,
    PeerRelation,
    Relation,
    Secret,
    State,
    Storage,
    StoredState,
    SubordinateRelation,
    _CharmSpec,
    _Event,
)


class MyCharm(CharmBase):
    pass


def assert_inconsistent(
    state: "State",
    event: "_Event",
    charm_spec: "_CharmSpec",
    juju_version="3.0",
):
    with pytest.raises(InconsistentScenarioError):
        check_consistency(state, event, charm_spec, juju_version)


def assert_consistent(
    state: "State",
    event: "_Event",
    charm_spec: "_CharmSpec",
    juju_version="3.0",
):
    check_consistency(state, event, charm_spec, juju_version)


def test_base():
    state = State()
    event = _Event("update_status")
    spec = _CharmSpec(MyCharm, {})
    assert_consistent(state, event, spec)


def test_workload_event_without_container():
    assert_inconsistent(
        State(),
        _Event("foo-pebble-ready", container=Container("foo")),
        _CharmSpec(MyCharm, {}),
    )
    assert_consistent(
        State(containers=[Container("foo")]),
        _Event("foo-pebble-ready", container=Container("foo")),
        _CharmSpec(MyCharm, {"containers": {"foo": {}}}),
    )


def test_container_meta_mismatch():
    assert_inconsistent(
        State(containers=[Container("bar")]),
        _Event("foo"),
        _CharmSpec(MyCharm, {"containers": {"baz": {}}}),
    )
    assert_consistent(
        State(containers=[Container("bar")]),
        _Event("foo"),
        _CharmSpec(MyCharm, {"containers": {"bar": {}}}),
    )


def test_container_in_state_but_no_container_in_meta():
    assert_inconsistent(
        State(containers=[Container("bar")]),
        _Event("foo"),
        _CharmSpec(MyCharm, {}),
    )
    assert_consistent(
        State(containers=[Container("bar")]),
        _Event("foo"),
        _CharmSpec(MyCharm, {"containers": {"bar": {}}}),
    )


def test_container_not_in_state():
    container = Container("bar")
    assert_inconsistent(
        State(),
        _Event("bar_pebble_ready", container=container),
        _CharmSpec(MyCharm, {"containers": {"bar": {}}}),
    )
    assert_consistent(
        State(containers=[container]),
        _Event("bar_pebble_ready", container=container),
        _CharmSpec(MyCharm, {"containers": {"bar": {}}}),
    )


def test_evt_bad_container_name():
    assert_inconsistent(
        State(),
        _Event("foo-pebble-ready", container=Container("bar")),
        _CharmSpec(MyCharm, {}),
    )
    assert_consistent(
        State(containers=[Container("bar")]),
        _Event("bar-pebble-ready", container=Container("bar")),
        _CharmSpec(MyCharm, {"containers": {"bar": {}}}),
    )


@pytest.mark.parametrize("suffix", RELATION_EVENTS_SUFFIX)
def test_evt_bad_relation_name(suffix):
    assert_inconsistent(
        State(),
        _Event(f"foo{suffix}", relation=Relation("bar")),
        _CharmSpec(MyCharm, {"requires": {"foo": {"interface": "xxx"}}}),
    )
    relation = Relation("bar")
    assert_consistent(
        State(relations=[relation]),
        _Event(f"bar{suffix}", relation=relation),
        _CharmSpec(MyCharm, {"requires": {"bar": {"interface": "xxx"}}}),
    )


@pytest.mark.parametrize("suffix", RELATION_EVENTS_SUFFIX)
def test_evt_no_relation(suffix):
    assert_inconsistent(State(), _Event(f"foo{suffix}"), _CharmSpec(MyCharm, {}))
    relation = Relation("bar")
    assert_consistent(
        State(relations=[relation]),
        _Event(f"bar{suffix}", relation=relation),
        _CharmSpec(MyCharm, {"requires": {"bar": {"interface": "xxx"}}}),
    )


def test_config_key_missing_from_meta():
    assert_inconsistent(
        State(config={"foo": True}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}),
    )
    assert_consistent(
        State(config={"foo": True}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "boolean"}}}),
    )


def test_bad_config_option_type():
    assert_inconsistent(
        State(config={"foo": True}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "string"}}}),
    )
    assert_inconsistent(
        State(config={"foo": True}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {}}}),
    )
    assert_consistent(
        State(config={"foo": True}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "boolean"}}}),
    )


@pytest.mark.parametrize(
    "config_type",
    (
        ("string", "foo", 1),
        ("int", 1, "1"),
        ("float", 1.0, 1),
        ("boolean", False, "foo"),
    ),
)
def test_config_types(config_type):
    type_name, valid_value, invalid_value = config_type
    assert_consistent(
        State(config={"foo": valid_value}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": type_name}}}),
    )
    assert_inconsistent(
        State(config={"foo": invalid_value}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": type_name}}}),
    )


@pytest.mark.parametrize("juju_version", ("3.4", "3.5", "4.0"))
def test_config_secret(juju_version):
    assert_consistent(
        State(config={"foo": "secret:co28kefmp25c77utl3n0"}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
        juju_version=juju_version,
    )
    assert_inconsistent(
        State(config={"foo": 1}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
    )
    assert_inconsistent(
        State(config={"foo": "co28kefmp25c77utl3n0"}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
    )
    assert_inconsistent(
        State(config={"foo": "secret:secret"}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
    )
    assert_inconsistent(
        State(config={"foo": "secret:co28kefmp25c77utl3n!"}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
    )


@pytest.mark.parametrize("juju_version", ("2.9", "3.3"))
def test_config_secret_old_juju(juju_version):
    assert_inconsistent(
        State(config={"foo": "secret:co28kefmp25c77utl3n0"}),
        _Event("bar"),
        _CharmSpec(MyCharm, {}, config={"options": {"foo": {"type": "secret"}}}),
        juju_version=juju_version,
    )


@pytest.mark.parametrize("bad_v", ("1.0", "0", "1.2", "2.35.42", "2.99.99", "2.99"))
def test_secrets_jujuv_bad(bad_v):
    secret = Secret("secret:foo", {0: {"a": "b"}})
    assert_inconsistent(
        State(secrets=[secret]),
        _Event("bar"),
        _CharmSpec(MyCharm, {}),
        bad_v,
    )
    assert_inconsistent(
        State(secrets=[secret]),
        secret.changed_event,
        _CharmSpec(MyCharm, {}),
        bad_v,
    )

    assert_inconsistent(
        State(),
        secret.changed_event,
        _CharmSpec(MyCharm, {}),
        bad_v,
    )


@pytest.mark.parametrize("good_v", ("3.0", "3.1", "3", "3.33", "4", "100"))
def test_secrets_jujuv_bad(good_v):
    assert_consistent(
        State(secrets=[Secret("secret:foo", {0: {"a": "b"}})]),
        _Event("bar"),
        _CharmSpec(MyCharm, {}),
        good_v,
    )


def test_secret_not_in_state():
    secret = Secret("secret:foo", {"a": "b"})
    assert_inconsistent(
        State(),
        _Event("secret_changed", secret=secret),
        _CharmSpec(MyCharm, {}),
    )
    assert_consistent(
        State(secrets=[secret]),
        _Event("secret_changed", secret=secret),
        _CharmSpec(MyCharm, {}),
    )


def test_peer_relation_consistency():
    assert_inconsistent(
        State(relations=[Relation("foo")]),
        _Event("bar"),
        _CharmSpec(MyCharm, {"peers": {"foo": {"interface": "bar"}}}),
    )
    assert_consistent(
        State(relations=[PeerRelation("foo")]),
        _Event("bar"),
        _CharmSpec(MyCharm, {"peers": {"foo": {"interface": "bar"}}}),
    )


def test_duplicate_endpoints_inconsistent():
    assert_inconsistent(
        State(),
        _Event("bar"),
        _CharmSpec(
            MyCharm,
            {
                "requires": {"foo": {"interface": "bar"}},
                "provides": {"foo": {"interface": "baz"}},
            },
        ),
    )


def test_sub_relation_consistency():
    assert_inconsistent(
        State(relations=[Relation("foo")]),
        _Event("bar"),
        _CharmSpec(
            MyCharm,
            {"requires": {"foo": {"interface": "bar", "scope": "container"}}},
        ),
    )

    assert_consistent(
        State(relations=[SubordinateRelation("foo")]),
        _Event("bar"),
        _CharmSpec(
            MyCharm,
            {"requires": {"foo": {"interface": "bar", "scope": "container"}}},
        ),
    )


def test_relation_sub_inconsistent():
    assert_inconsistent(
        State(relations=[SubordinateRelation("foo")]),
        _Event("bar"),
        _CharmSpec(MyCharm, {"requires": {"foo": {"interface": "bar"}}}),
    )


def test_relation_not_in_state():
    relation = Relation("foo")
    assert_inconsistent(
        State(),
        _Event("foo_relation_changed", relation=relation),
        _CharmSpec(MyCharm, {"requires": {"foo": {"interface": "bar"}}}),
    )
    assert_consistent(
        State(relations=[relation]),
        _Event("foo_relation_changed", relation=relation),
        _CharmSpec(MyCharm, {"requires": {"foo": {"interface": "bar"}}}),
    )


def test_dupe_containers_inconsistent():
    assert_inconsistent(
        State(containers=[Container("foo"), Container("foo")]),
        _Event("bar"),
        _CharmSpec(MyCharm, {"containers": {"foo": {}}}),
    )


def test_action_not_in_meta_inconsistent():
    action = Action("foo", params={"bar": "baz"})
    assert_inconsistent(
        State(),
        action.event,
        _CharmSpec(MyCharm, meta={}, actions={}),
    )


def test_action_meta_type_inconsistent():
    action = Action("foo", params={"bar": "baz"})
    assert_inconsistent(
        State(),
        action.event,
        _CharmSpec(
            MyCharm, meta={}, actions={"foo": {"params": {"bar": {"type": "zabazaba"}}}}
        ),
    )

    assert_inconsistent(
        State(),
        action.event,
        _CharmSpec(MyCharm, meta={}, actions={"foo": {"params": {"bar": {}}}}),
    )


def test_action_name():
    action = Action("foo", params={"bar": "baz"})

    assert_consistent(
        State(),
        action.event,
        _CharmSpec(
            MyCharm, meta={}, actions={"foo": {"params": {"bar": {"type": "string"}}}}
        ),
    )
    assert_inconsistent(
        State(),
        _Event("box_action", action=action),
        _CharmSpec(MyCharm, meta={}, actions={"foo": {}}),
    )


_ACTION_TYPE_CHECKS = [
    ("string", "baz", None),
    ("boolean", True, "baz"),
    ("integer", 42, 1.5),
    ("number", 28.8, "baz"),
    ("array", ["a", "b", "c"], 1.5),  # A string is an acceptable array.
    ("object", {"k": "v"}, "baz"),
]


@pytest.mark.parametrize("ptype,good,bad", _ACTION_TYPE_CHECKS)
def test_action_params_type(ptype, good, bad):
    action = Action("foo", params={"bar": good})
    assert_consistent(
        State(),
        action.event,
        _CharmSpec(
            MyCharm, meta={}, actions={"foo": {"params": {"bar": {"type": ptype}}}}
        ),
    )
    if bad is not None:
        action = Action("foo", params={"bar": bad})
        assert_inconsistent(
            State(),
            action.event,
            _CharmSpec(
                MyCharm, meta={}, actions={"foo": {"params": {"bar": {"type": ptype}}}}
            ),
        )


def test_duplicate_relation_ids():
    assert_inconsistent(
        State(relations=[Relation("foo", id=1), Relation("bar", id=1)]),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "requires": {"foo": {"interface": "foo"}, "bar": {"interface": "bar"}}
            },
        ),
    )


def test_relation_without_endpoint():
    assert_inconsistent(
        State(relations=[Relation("foo", id=1), Relation("bar", id=1)]),
        _Event("start"),
        _CharmSpec(MyCharm, meta={"name": "charlemagne"}),
    )

    assert_consistent(
        State(relations=[Relation("foo", id=1), Relation("bar", id=2)]),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "requires": {"foo": {"interface": "foo"}, "bar": {"interface": "bar"}}
            },
        ),
    )


def test_storage_event():
    storage = Storage("foo")
    assert_inconsistent(
        State(storage=[storage]),
        _Event("foo-storage-attached"),
        _CharmSpec(MyCharm, meta={"name": "rupert"}),
    )
    assert_inconsistent(
        State(storage=[storage]),
        _Event("foo-storage-attached"),
        _CharmSpec(
            MyCharm, meta={"name": "rupert", "storage": {"foo": {"type": "filesystem"}}}
        ),
    )


def test_storage_states():
    storage1 = Storage("foo", index=1)
    storage2 = Storage("foo", index=1)

    assert_inconsistent(
        State(storage=[storage1, storage2]),
        _Event("start"),
        _CharmSpec(MyCharm, meta={"name": "everett"}),
    )
    assert_consistent(
        State(storage=[storage1, dataclasses.replace(storage2, index=2)]),
        _Event("start"),
        _CharmSpec(
            MyCharm, meta={"name": "frank", "storage": {"foo": {"type": "filesystem"}}}
        ),
    )
    assert_consistent(
        State(storage=[storage1, dataclasses.replace(storage2, name="marx")]),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "engels",
                "storage": {
                    "foo": {"type": "filesystem"},
                    "marx": {"type": "filesystem"},
                },
            },
        ),
    )


def test_storage_not_in_state():
    storage = Storage("foo")
    assert_inconsistent(
        State(),
        _Event("foo_storage_attached", storage=storage),
        _CharmSpec(
            MyCharm,
            meta={"name": "sam", "storage": {"foo": {"type": "filesystem"}}},
        ),
    )
    assert_consistent(
        State(storage=[storage]),
        _Event("foo_storage_attached", storage=storage),
        _CharmSpec(
            MyCharm,
            meta={"name": "sam", "storage": {"foo": {"type": "filesystem"}}},
        ),
    )


def test_resource_states():
    # happy path
    assert_consistent(
        State(resources={"foo": "/foo/bar.yaml"}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={"name": "yamlman", "resources": {"foo": {"type": "oci-image"}}},
        ),
    )

    # no resources in state but some in meta: OK. Not realistic wrt juju but fine for testing
    assert_consistent(
        State(),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={"name": "yamlman", "resources": {"foo": {"type": "oci-image"}}},
        ),
    )

    # resource not defined in meta
    assert_inconsistent(
        State(resources={"bar": "/foo/bar.yaml"}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={"name": "yamlman", "resources": {"foo": {"type": "oci-image"}}},
        ),
    )

    assert_inconsistent(
        State(resources={"bar": "/foo/bar.yaml"}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={"name": "yamlman"},
        ),
    )


def test_networks_consistency():
    assert_inconsistent(
        State(networks={"foo": Network.default()}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={"name": "wonky"},
        ),
    )

    assert_inconsistent(
        State(networks={"foo": Network.default()}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "pinky",
                "extra-bindings": {"foo": {}},
                "requires": {"foo": {"interface": "bar"}},
            },
        ),
    )

    assert_consistent(
        State(networks={"foo": Network.default()}),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "pinky",
                "extra-bindings": {"foo": {}},
                "requires": {"bar": {"interface": "bar"}},
            },
        ),
    )


def test_storedstate_consistency():
    assert_consistent(
        State(
            stored_state=[
                StoredState(None, content={"foo": "bar"}),
                StoredState(None, "my_stored_state", content={"foo": 1}),
                StoredState("MyCharmLib", content={"foo": None}),
                StoredState("OtherCharmLib", content={"foo": (1, 2, 3)}),
            ]
        ),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "foo",
            },
        ),
    )
    assert_inconsistent(
        State(
            stored_state=[
                StoredState(None, content={"foo": "bar"}),
                StoredState(None, "_stored", content={"foo": "bar"}),
            ]
        ),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "foo",
            },
        ),
    )
    assert_inconsistent(
        State(stored_state=[StoredState(None, content={"secret": Secret("foo", {})})]),
        _Event("start"),
        _CharmSpec(
            MyCharm,
            meta={
                "name": "foo",
            },
        ),
    )
