import pytest
from ops.charm import ActionEvent, CharmBase, StartEvent
from ops.framework import Framework

from scenario import Context
from scenario.state import Event, State, _CharmSpec
from tests.helpers import trigger


@pytest.fixture(scope="function")
def charm_evts():
    events = []

    class MyCharm(CharmBase):
        def __init__(self, framework: Framework):
            super().__init__(framework)
            for evt in self.on.events().values():
                self.framework.observe(evt, self._on_event)

            print(self.on.show_proxied_endpoints_action)

        def _on_event(self, event):
            events.append(event)

    return MyCharm, events


def test_start_event(charm_evts):
    charm, evts = charm_evts
    trigger(
        State(),
        event="start",
        charm_type=charm,
        meta={"name": "foo"},
        actions={"show_proxied_endpoints": {}},
    )
    assert len(evts) == 1
    assert isinstance(evts[0], StartEvent)


class MyCharm(CharmBase):
    _order = []

    def __init__(self, framework: Framework):
        super().__init__(framework)
        for evt in self.on.events().values():
            self.framework.observe(evt, self._on_event_1)
            self.framework.observe(evt, self._on_event_2)
            self.framework.observe(evt, self._on_event_3)

    def _on_event_1(self, _):
        self._order.append(1)

    def _on_event_2(self, _):
        self._order.append(2)

    def _on_event_3(self, _):
        self._order.append(3)


def test_observed_event_emission_ordering():
    ctx = Context(MyCharm, meta={"name": "foo"})
    ctx.run("start", State())
    assert MyCharm._order == [1, 2, 3]
