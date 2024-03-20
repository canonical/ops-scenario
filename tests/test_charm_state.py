from dataclasses import dataclass

import pytest
from ops import CharmBase, Framework

from scenario import Context
from scenario.charm_state import CharmStateBackend
from scenario.state import CharmState, State


@dataclass(frozen=True)
class MyState(CharmState):
    foo: int = 10
    bar: str = "10"


class MyCharmStateBackend(CharmStateBackend):
    @property
    def foo(self) -> int:
        return 10

    @foo.setter
    def foo(self, val: int):
        pass

    @property
    def bar(self) -> str:
        return "10"


class MyCharm(CharmBase):
    state = MyCharmStateBackend()

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.foo = self.state.foo
        self.bar = self.state.bar


@pytest.fixture
def ctx():
    return Context(MyCharm, meta={"name": "foo"})


@pytest.mark.parametrize("attr", ("foo", "bar"))
@pytest.mark.parametrize("val", (1, 10, 20))
def test_get(ctx, attr, val):
    state = State(charm=MyState(name="state", foo=val, bar=str(val)))

    with ctx.manager("start", state=state) as mgr:
        charm = mgr.charm
        assert charm.foo == val
        assert charm.bar == str(val)
