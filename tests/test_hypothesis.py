from hypothesis import given
from hypothesis import strategies as st
from ops import CharmBase, Framework

from scenario import State, Context
from scenario.strategies import Plugin


class MyCharm(CharmBase):
    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.config["foo"]
        self.config["bar"]


config_plugin = Plugin(
    MyCharm,
    meta={"name": "george"},
    config={
        "options": {
            "foo": {"type": "string", "default": "bar"},
            "bar": {"type": "string"},
        }
    },
)


@given(config_plugin.context, st.builds(State, config=config_plugin.configs))
def test_configs_hypothesis(ctx, s):
    ctx.run("start", s)


relation_plugin = Plugin(
    MyCharm,
    meta={
        "name": "george",
        "provides": {
            "foo": {"interface": "bar"},
            "baz": {"interface": "qux", "limit": 1},
        },
        "requires": {
            "dead": {"interface": "beef", "scope": "container"},
        },
        "peers": {"cluster": {"interface": "whinnie"}},
    },
)


@given(relation_plugin.context, st.builds(State, relations=relation_plugin.relations))
def test_relations_hypothesis(ctx, s):
    ctx.run("start", s)
