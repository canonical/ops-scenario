from unittest.mock import patch

import pytest
from ops import CharmBase

from scenario import Context, State, Task
from scenario.state import _Event, next_action_id


class MyCharm(CharmBase):
    pass


def test_run():
    ctx = Context(MyCharm, meta={"name": "foo"})
    state = State()

    with patch.object(ctx, "_run") as p:
        ctx._output_state = "foo"  # would normally be set within the _run call scope
        output = ctx.run(ctx.on.start(), state)
        assert output == "foo"

    assert p.called
    e = p.call_args.kwargs["event"]
    s = p.call_args.kwargs["state"]

    assert isinstance(e, _Event)
    assert e.name == "start"
    assert s is state


def test_run_action():
    ctx = Context(MyCharm, meta={"name": "foo"})
    state = State()
    expected_id = next_action_id(update=False)

    with patch.object(ctx, "_run") as p:
        ctx._output_state = "foo"  # would normally be set within the _run call scope
        output = ctx.run(ctx.on.action("do-foo"), state)
        assert output == "foo"

    assert p.called
    e = p.call_args.kwargs["event"]
    s = p.call_args.kwargs["state"]

    assert isinstance(e, _Event)
    assert e.name == "do_foo_action"
    assert s is state
    assert e.action.id == expected_id


@pytest.mark.parametrize("app_name", ("foo", "bar", "george"))
@pytest.mark.parametrize("unit_id", (1, 2, 42))
def test_app_name(app_name, unit_id):
    ctx = Context(MyCharm, meta={"name": "foo"}, app_name=app_name, unit_id=unit_id)
    with ctx(ctx.on.start(), State()) as mgr:
        assert mgr.charm.app.name == app_name
        assert mgr.charm.unit.name == f"{app_name}/{unit_id}"


def test_context_manager():
    ctx = Context(MyCharm, meta={"name": "foo"}, actions={"act": {}})
    state = State()
    with ctx(ctx.on.start(), state) as mgr:
        mgr.run()
        assert mgr.charm.meta.name == "foo"

    with ctx(ctx.on.action("act"), state) as mgr:
        mgr.run()
        assert mgr.charm.meta.name == "foo"


def test_task_no_positional_arguments():
    with pytest.raises(TypeError):
        Task(None)


def test_action_output_no_results():
    class MyCharm(CharmBase):
        def __init__(self, framework):
            super().__init__(framework)
            framework.observe(self.on.act_action, self._on_act_action)

        def _on_act_action(self, _):
            pass

    ctx = Context(MyCharm, meta={"name": "foo"}, actions={"act": {}})
    ctx.run(ctx.on.action("act"), State())
    assert len(ctx.action_history) == 1
    task = ctx.action_history[0]
    assert task.results is None
    assert task.status == "completed"
    assert task.failure_message == ""
