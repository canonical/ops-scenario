import pytest
from ops import __version__ as ops_version
from ops.charm import ActionEvent, CharmBase
from ops.framework import Framework

from scenario import Context
from scenario.context import InvalidEventError
from scenario.state import State, _Action, next_action_id


@pytest.fixture(scope="function")
def mycharm():
    class MyCharm(CharmBase):
        _evt_handler = None

        def __init__(self, framework: Framework):
            super().__init__(framework)
            for evt in self.on.events().values():
                self.framework.observe(evt, self._on_event)

        def _on_event(self, event):
            if handler := self._evt_handler:
                handler(event)

    return MyCharm


@pytest.mark.parametrize("baz_value", (True, False))
def test_action_event(mycharm, baz_value):
    ctx = Context(
        mycharm,
        meta={"name": "foo"},
        actions={
            "foo": {"params": {"bar": {"type": "number"}, "baz": {"type": "boolean"}}}
        },
    )
    ctx.run_action(ctx.on.action("foo", params={"baz": baz_value, "bar": 10}), State())

    evt = ctx.emitted_events[0]

    assert evt.params["bar"] == 10
    assert evt.params["baz"] is baz_value


@pytest.mark.parametrize("res_value", ("one", 1, [2], ["bar"], (1,), {1, 2}))
def test_action_event_results_invalid(mycharm, res_value):
    def handle_evt(charm: CharmBase, evt: ActionEvent):
        with pytest.raises((TypeError, AttributeError)):
            evt.set_results(res_value)

    mycharm._evt_handler = handle_evt

    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})
    ctx.run_action(ctx.on.action("foo"), State())


def test_cannot_run_action(mycharm):
    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})

    with pytest.raises(InvalidEventError):
        ctx.run(ctx.on.action("foo"), state=State())


@pytest.mark.parametrize("res_value", ({"a": {"b": {"c"}}}, {"d": "e"}))
def test_action_event_results_valid(mycharm, res_value):
    def handle_evt(charm: CharmBase, evt):
        if not isinstance(evt, ActionEvent):
            return
        evt.set_results(res_value)
        evt.log("foo")
        evt.log("bar")

    mycharm._evt_handler = handle_evt

    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})

    out = ctx.run_action(ctx.on.action("foo"), State())

    assert out.results == res_value
    assert out.success is True


@pytest.mark.parametrize("res_value", ({"a": {"b": {"c"}}}, {"d": "e"}))
def test_action_event_outputs(mycharm, res_value):
    def handle_evt(charm: CharmBase, evt: ActionEvent):
        if not isinstance(evt, ActionEvent):
            return

        evt.set_results({"my-res": res_value})
        evt.log("log1")
        evt.log("log2")
        evt.fail("failed becozz")

    mycharm._evt_handler = handle_evt

    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})
    out = ctx.run_action(ctx.on.action("foo"), State())

    assert out.failure == "failed becozz"
    assert out.logs == ["log1", "log2"]
    assert out.success is False


def _ops_less_than(wanted_major, wanted_minor):
    major, minor = (int(v) for v in ops_version.split(".")[:2])
    if major < wanted_major:
        return True
    if major == wanted_major and minor < wanted_minor:
        return True
    return False


@pytest.mark.skipif(
    _ops_less_than(2, 11), reason="ops 2.10 and earlier don't have ActionEvent.id"
)
def test_action_event_has_id(mycharm):
    def handle_evt(charm: CharmBase, evt: ActionEvent):
        if not isinstance(evt, ActionEvent):
            return
        assert isinstance(evt.id, str) and evt.id != ""

    mycharm._evt_handler = handle_evt

    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})
    ctx.run_action(ctx.on.action("foo"), State())


@pytest.mark.skipif(
    _ops_less_than(2, 11), reason="ops 2.10 and earlier don't have ActionEvent.id"
)
def test_action_event_has_override_id(mycharm):
    uuid = "0ddba11-cafe-ba1d-5a1e-dec0debad"

    def handle_evt(charm: CharmBase, evt: ActionEvent):
        if not isinstance(evt, ActionEvent):
            return
        assert evt.id == uuid

    mycharm._evt_handler = handle_evt

    ctx = Context(mycharm, meta={"name": "foo"}, actions={"foo": {}})
    ctx.run_action(ctx.on.action("foo", id=uuid), State())


def test_positional_arguments():
    with pytest.raises(TypeError):
        _Action("foo", {})


def test_default_arguments():
    expected_id = next_action_id(update=False)
    name = "foo"
    action = _Action(name)
    assert action.name == name
    assert action.params == {}
    assert action.id == expected_id
