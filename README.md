# Scenario

[![Build](https://github.com/canonical/ops-scenario/actions/workflows/build_wheels.yaml/badge.svg)](https://github.com/canonical/ops-scenario/actions/workflows/build_wheels.yaml)
[![QC](https://github.com/canonical/ops-scenario/actions/workflows/quality_checks.yaml/badge.svg?event=pull_request)](https://github.com/canonical/ops-scenario/actions/workflows/quality_checks.yaml?event=pull_request)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)
[![foo](https://img.shields.io/badge/everything-charming-blueviolet)](https://github.com/PietroPasotti/jhack)
[![Awesome](https://cdn.rawgit.com/sindresorhus/awesome/d7305f38d29fed78fa85652e3a63e154dd8e8829/media/badge.svg)](https://discourse.charmhub.io/t/rethinking-charm-testing-with-ops-scenario/8649)

Scenario is a state-transition, functional testing framework for Operator Framework charms.

Where the Harness enables you to procedurally mock pieces of the state the charm needs to function, Scenario tests allow
you to declaratively define the state all at once, and use it as a sort of context against which you can fire a single
event on the charm and execute its logic.

This puts scenario tests somewhere in between unit and integration tests: some say 'functional', some say 'contract'.

Scenario tests nudge you into thinking of a charm as an input->output function. Input is what we call a `Scene`: the
union of an `Event` (why am I being executed) and a `State` (am I leader? what is my relation data? what is my
config?...). The output is another context instance: the context after the charm has had a chance to interact with the
mocked juju model and affect the state back.

![state transition model depiction](resources/state-transition-model.png)

Scenario-testing a charm, then, means verifying that:

- the charm does not raise uncaught exceptions while handling the scene
- the output state (or the diff with the input state) is as expected.

# Core concepts as a metaphor

I like metaphors, so here we go:

- There is a theatre stage.
- You pick an actor (a Charm) to put on the stage. Not just any actor: an improv one.
- You arrange the stage with content that the actor will have to interact with. This consists of selecting:
    - An initial situation (State) in which the actor is, e.g. is the actor the main role or an NPC (is_leader), or what
      other actors are there around it, what is written in those pebble-shaped books on the table?
    - Something that has just happened (an Event) and to which the actor has to react (e.g. one of the NPCs leaves the
      stage (relation-departed), or the content of one of the books changes).
- How the actor will react to the event will have an impact on the context: e.g. the actor might knock over a table (a
  container), or write something down into one of the books.

# Core concepts not as a metaphor

Scenario tests are about running assertions on atomic state transitions treating the charm being tested like a black
box. An initial state goes in, an event occurs (say, `'start'`) and a new state comes out. Scenario tests are about
validating the transition, that is, consistency-checking the delta between the two states, and verifying the charm
author's expectations.

Comparing scenario tests with `Harness` tests:

- Harness exposes an imperative API: the user is expected to call methods on the Harness driving it to the desired
  state, then verify its validity by calling charm methods or inspecting the raw data.
- Harness instantiates the charm once, then allows you to fire multiple events on the charm, which is breeding ground
  for subtle bugs. Scenario tests are centered around testing single state transitions, that is, one event at a time.
  This ensures that the execution environment is as clean as possible (for a unit test).
- Harness maintains a model of the juju Model, which is a maintenance burden and adds complexity. Scenario mocks at the
  level of hook tools and stores all mocking data in a monolithic data structure (the State), which makes it more
  lightweight and portable.
- TODO: Scenario can mock at the level of hook tools. Decoupling charm and context allows us to swap out easily any part
  of this flow, and even share context data across charms, codebases, teams...

# Writing scenario tests

A scenario test consists of three broad steps:

- **Arrange**:
    - declare the input state
    - select an event to fire
- **Act**:
    - run the state (i.e. obtain the output state)
    - optionally, use pre-event and post-event hooks to get a hold of the charm instance and run assertions on internal
      APIs
- **Assert**:
    - verify that the output state is how you expect it to be
    - optionally, verify that the delta with the input state is what you expect it to be

The most basic scenario is the so-called `null scenario`: one in which all is defaulted and barely any data is
available. The charm has no config, no relations, no networks, and no leadership.

With that, we can write the simplest possible scenario test:

```python
from scenario import State, Context
from ops.charm import CharmBase
from ops.model import UnknownStatus


class MyCharm(CharmBase):
    pass


def test_scenario_base():
    ctx = Context(MyCharm,
                  meta={"name": "foo"})
    out = ctx.run('start', State())
    assert out.status.unit == UnknownStatus()
```

Now let's start making it more complicated. Our charm sets a special state if it has leadership on 'start':

```python
import pytest
from scenario import State, Context
from ops.charm import CharmBase
from ops.model import ActiveStatus


class MyCharm(CharmBase):
    def __init__(self, ...):
        self.framework.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        if self.unit.is_leader():
            self.unit.status = ActiveStatus('I rule')
        else:
            self.unit.status = ActiveStatus('I am ruled')


@pytest.mark.parametrize('leader', (True, False))
def test_status_leader(leader):
    ctx = Context(MyCharm,
                  meta={"name": "foo"})
    out = ctx.run('start',
                  State(leader=leader)
    assert out.status.unit == ActiveStatus('I rule' if leader else 'I am ruled')
```

By defining the right state we can programmatically define what answers will the charm get to all the questions it can
ask the juju model: am I leader? What are my relations? What is the remote unit I'm talking to? etc...

## Statuses

One of the simplest types of black-box testing available to charmers is to execute the charm and verify that the charm
sets the expected unit/application status. We have seen a simple example above including leadership. But what if the
charm transitions through a sequence of statuses?

```python
from ops.model import MaintenanceStatus, ActiveStatus, WaitingStatus, BlockedStatus


# charm code:
def _on_event(self, _event):
    self.unit.status = MaintenanceStatus('determining who the ruler is...')
    try:
        if self._call_that_takes_a_few_seconds_and_only_passes_on_leadership:
            self.unit.status = ActiveStatus('I rule')
        else:
            self.unit.status = WaitingStatus('checking this is right...')
            self._check_that_takes_some_more_time()
            self.unit.status = ActiveStatus('I am ruled')
    except:
        self.unit.status = BlockedStatus('something went wrong')
```

You can verify that the charm has followed the expected path by checking the **unit status history** like so:

```python
from charm import MyCharm
from ops.model import MaintenanceStatus, ActiveStatus, WaitingStatus, UnknownStatus
from scenario import State, Context


def test_statuses():
    ctx = Context(MyCharm,
                  meta={"name": "foo"})
    out = ctx.run('start',
                  State(leader=False))
    assert out.status.unit_history == [
        UnknownStatus(),
        MaintenanceStatus('determining who the ruler is...'),
        WaitingStatus('checking this is right...'),
        ActiveStatus("I am ruled"),
    ]
```

Note that the current status is not in the **unit status history**.

Also note that, unless you initialize the State with a preexisting status, the first status in the history will always
be `unknown`. That is because, so far as scenario is concerned, each event is "the first event this charm has ever
seen".

If you want to simulate a situation in which the charm already has seen some event, and is in a status other than
Unknown (the default status every charm is born with), you will have to pass the 'initial status' to State.

```python
from ops.model import ActiveStatus
from scenario import State, Status

State(leader=False, status=Status(unit=ActiveStatus('foo')))
```

## Relations

You can write scenario tests to verify the shape of relation data:

```python
from ops.charm import CharmBase

from scenario import Relation, State, Context


# This charm copies over remote app data to local unit data
class MyCharm(CharmBase):
    ...

    def _on_event(self, e):
        rel = e.relation
        assert rel.app.name == 'remote'
        assert rel.data[self.unit]['abc'] == 'foo'
        rel.data[self.unit]['abc'] = rel.data[e.app]['cde']


def test_relation_data():
    state_in = State(relations=[
        Relation(
            endpoint="foo",
            interface="bar",
            remote_app_name="remote",
            local_unit_data={"abc": "foo"},
            remote_app_data={"cde": "baz!"},
        ),
    ])
    ctx = Context(MyCharm,
                  meta={"name": "foo"})

    state_out = ctx.run('start', state_in)

    assert state_out.relations[0].local_unit_data == {"abc": "baz!"}
    # you can do this to check that there are no other differences:
    assert state_out.relations == [
        Relation(
            endpoint="foo",
            interface="bar",
            remote_app_name="remote",
            local_unit_data={"abc": "baz!"},
            remote_app_data={"cde": "baz!"},
        ),
    ]

# which is very idiomatic and superbly explicit. Noice.
```

The only mandatory argument to `Relation` (and other relation types, see below) is `endpoint`. The `interface` will be
derived from the charm's `metadata.yaml`. When fully defaulted, a relation is 'empty'. There are no remote units, the
remote application is called `'remote'` and only has a single unit `remote/0`, and nobody has written any data to the
databags yet.

That is typically the state of a relation when the first unit joins it.

When you use `Relation`, you are specifying a regular (conventional) relation. But that is not the only type of
relation. There are also peer relations and subordinate relations. While in the background the data model is the same,
the data access rules and the consistency constraints on them are very different. For example, it does not make sense
for a peer relation to have a different 'remote app' than its 'local app', because it's the same application.

### PeerRelation

To declare a peer relation, you should use `scenario.state.PeerRelation`. The core difference with regular relations is
that peer relations do not have a "remote app" (it's this app, in fact). So unlike `Relation`, a `PeerRelation` does not
have `remote_app_name` or `remote_app_data` arguments. Also, it talks in terms of `peers`:

- `Relation.remote_unit_ids` maps to `PeerRelation.peers_ids`
- `Relation.remote_units_data` maps to `PeerRelation.peers_data`

```python
from scenario.state import PeerRelation

relation = PeerRelation(
    endpoint="peers",
    peers_data={1: {}, 2: {}, 42: {'foo': 'bar'}},
)
```

be mindful when using `PeerRelation` not to include **"this unit"**'s ID in `peers_data` or `peers_ids`, as that would
be flagged by the Consistency Checker:

```python
from scenario import State, PeerRelation, Context

state_in = State(relations=[
    PeerRelation(
        endpoint="peers",
        peers_data={1: {}, 2: {}, 42: {'foo': 'bar'}},
    )],
    unit_id=1)

Context(...).run("start", state_in)  # invalid: this unit's id cannot be the ID of a peer.


```

### SubordinateRelation

To declare a subordinate relation, you should use `scenario.state.SubordinateRelation`. The core difference with regular
relations is that subordinate relations always have exactly one remote unit (there is always exactly one primary unit
that this unit can see). So unlike `Relation`, a `SubordinateRelation` does not have a `remote_units_data` argument.
Instead, it has a `remote_unit_data` taking a single `Dict[str:str]`, and takes the primary unit ID as a separate
argument. Also, it talks in terms of `primary`:

- `Relation.remote_unit_ids` becomes `SubordinateRelation.primary_id` (a single ID instead of a list of IDs)
- `Relation.remote_units_data` becomes `SubordinateRelation.remote_unit_data` (a single databag instead of a mapping
  from unit IDs to databags)
- `Relation.remote_app_name` maps to `SubordinateRelation.primary_app_name`

```python
from scenario.state import SubordinateRelation

relation = SubordinateRelation(
  endpoint="peers",
  remote_unit_data={"foo": "bar"},
  remote_app_name="zookeeper",
  remote_unit_id=42
)
relation.remote_unit_name  # "zookeeper/42"
```

## Triggering Relation Events

If you want to trigger relation events, the easiest way to do so is get a hold of the Relation instance and grab the
event from one of its aptly-named properties:

```python
from scenario import Relation

relation = Relation(endpoint="foo", interface="bar")
changed_event = relation.changed_event
joined_event = relation.joined_event
# ...
```

This is in fact syntactic sugar for:

```python
from scenario import Relation, Event

relation = Relation(endpoint="foo", interface="bar")
changed_event = Event('foo-relation-changed', relation=relation)
```

The reason for this construction is that the event is associated with some relation-specific metadata, that Scenario
needs to set up the process that will run `ops.main` with the right environment variables.

### Additional event parameters

All relation events have some additional metadata that does not belong in the Relation object, such as, for a
relation-joined event, the name of the (remote) unit that is joining the relation. That is what determines what
`ops.model.Unit` you get when you get `RelationJoinedEvent().unit` in an event handler.

In order to supply this parameter, you will have to **call** the event object and pass as `remote_unit_id` the id of the
remote unit that the event is about. The reason that this parameter is not supplied to `Relation` but to relation
events, is that the relation already ties 'this app' to some 'remote app' (cfr. the `Relation.remote_app_name` attr),
but not to a specific unit. What remote unit this event is about is not a `State` concern but an `Event` one.

The `remote_unit_id` will default to the first ID found in the relation's `remote_unit_ids`, but if the test you are
writing is close to that domain, you should probably override it and pass it manually.

```python
from scenario import Relation, Event

relation = Relation(endpoint="foo", interface="bar")
remote_unit_2_is_joining_event = relation.joined_event(remote_unit_id=2)

# which is syntactic sugar for:
remote_unit_2_is_joining_event = Event('foo-relation-changed', relation=relation, relation_remote_unit_id=2)
```

## Containers

When testing a kubernetes charm, you can mock container interactions. When using the null state (`State()`), there will
be no containers. So if the charm were to `self.unit.containers`, it would get back an empty dict.

To give the charm access to some containers, you need to pass them to the input state, like so:
`State(containers=[...])`

An example of a scene including some containers:

```python
from scenario.state import Container, State

state = State(containers=[
    Container(name="foo", can_connect=True),
    Container(name="bar", can_connect=False)
])
```

In this case, `self.unit.get_container('foo').can_connect()` would return `True`, while for 'bar' it would give `False`.

You can configure a container to have some files in it:

```python
from pathlib import Path

from scenario.state import Container, State, Mount

local_file = Path('/path/to/local/real/file.txt')

state = State(containers=[
    Container(name="foo",
              can_connect=True,
              mounts={'local': Mount('/local/share/config.yaml', local_file)})
]
)
```

In this case, if the charm were to:

```python
def _on_start(self, _):
    foo = self.unit.get_container('foo')
    content = foo.pull('/local/share/config.yaml').read()
```

then `content` would be the contents of our locally-supplied `file.txt`. You can use `tempdir` for nicely wrapping
strings and passing them to the charm via the container.

`container.push` works similarly, so you can write a test like:

```python
import tempfile
from ops.charm import CharmBase
from scenario import State, Container, Mount, Context


class MyCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.foo_pebble_ready, self._on_pebble_ready)

    def _on_pebble_ready(self, _):
        foo = self.unit.get_container('foo')
        foo.push('/local/share/config.yaml', "TEST", make_dirs=True)


def test_pebble_push():
    with tempfile.NamedTemporaryFile() as local_file:
        container = Container(name='foo',
                              can_connect=True,
                              mounts={'local': Mount('/local/share/config.yaml', local_file.name)})
        state_in = State(
            containers=[container]
        )
        Context(
            MyCharm,
            meta={"name": "foo", "containers": {"foo": {}}}).run(
            "start",
            state_in,
        )
        assert local_file.read().decode() == "TEST"
```

`container.pebble_ready_event` is syntactic sugar for: `Event("foo-pebble-ready", container=container)`. The reason we
need to associate the container with the event is that the Framework uses an envvar to determine which container the
pebble-ready event is about (it does not use the event name). Scenario needs that information, similarly, for injecting
that envvar into the charm's runtime.

`container.exec` is a tad more complicated, but if you get to this low a level of simulation, you probably will have far
worse issues to deal with. You need to specify, for each possible command the charm might run on the container, what the
result of that would be: its return code, what will be written to stdout/stderr.

```python
from ops.charm import CharmBase

from scenario import State, Container, ExecOutput, Context

LS_LL = """
.rw-rw-r--  228 ubuntu ubuntu 18 jan 12:05 -- charmcraft.yaml
.rw-rw-r--  497 ubuntu ubuntu 18 jan 12:05 -- config.yaml
.rw-rw-r--  900 ubuntu ubuntu 18 jan 12:05 -- CONTRIBUTING.md
drwxrwxr-x    - ubuntu ubuntu 18 jan 12:06 -- lib
"""


class MyCharm(CharmBase):
    def _on_start(self, _):
        foo = self.unit.get_container('foo')
        proc = foo.exec(['ls', '-ll'])
        stdout, _ = proc.wait_output()
        assert stdout == LS_LL


def test_pebble_exec():
    container = Container(
        name='foo',
        exec_mock={
            ('ls', '-ll'):  # this is the command we're mocking
                ExecOutput(return_code=0,  # this data structure contains all we need to mock the call.
                           stdout=LS_LL)
        }
    )
    state_in = State(
        containers=[container]
    )
    state_out = Context(
        MyCharm,
        meta={"name": "foo", "containers": {"foo": {}}},
    ).run(
        container.pebble_ready_event,
        state_in,
    )
```

# Secrets

Scenario has secrets. Here's how you use them.

```python
from scenario import State, Secret

state = State(
    secrets=[
        Secret(
            id='foo',
            contents={0: {'key': 'public'}}
        )
    ]
)
```

The only mandatory arguments to Secret are its secret ID (which should be unique) and its 'contents': that is, a mapping from revision numbers (integers) to a str:str dict representing the payload of the revision. 

By default, the secret is not owned by **this charm** nor is it granted to it. 
Therefore, if charm code attempted to get that secret revision, it would get a permission error: we didn't grant it to this charm, nor we specified that the secret is owned by it.

To specify a secret owned by this unit (or app):
```python
from scenario import State, Secret

state = State(
    secrets=[
        Secret(
            id='foo',
            contents={0: {'key': 'public'}},
            owner='unit',  # or 'app'
            remote_grants = {0: {"remote"}}  # the secret owner has granted access to the "remote" app over some relation with ID 0
        )
    ]
)
```

To specify a secret owned by some other application and give this unit (or app) access to it:
```python
from scenario import State, Secret

state = State(
    secrets=[
        Secret(
            id='foo',
            contents={0: {'key': 'public'}},
            # owner=None, which is the default
            granted="unit",  # or "app",
            revision=0,  # the revision that this unit (or app) is currently tracking
        )
    ]
)
```

# Deferred events

Scenario allows you to accurately simulate the Operator Framework's event queue. The event queue is responsible for
keeping track of the deferred events. On the input side, you can verify that if the charm triggers with this and that
event in its queue (they would be there because they had been deferred in the previous run), then the output state is
valid.

```python
from scenario import State, deferred, Context


class MyCharm(...):
    ...

    def _on_update_status(self, e):
        e.defer()

    def _on_start(self, e):
        e.defer()


def test_start_on_deferred_update_status(MyCharm):
    """Test charm execution if a 'start' is dispatched when in the previous run an update-status had been deferred."""
    state_in = State(
        deferred=[
            deferred('update_status',
                     handler=MyCharm._on_update_status)
        ]
    )
    state_out = Context(MyCharm).run('start', state_in)
    assert len(state_out.deferred) == 1
    assert state_out.deferred[0].name == 'start'
```

You can also generate the 'deferred' data structure (called a DeferredEvent) from the corresponding Event (and the
handler):

```python
from scenario import Event, Relation


class MyCharm(...):
    ...


deferred_start = Event('start').deferred(MyCharm._on_start)
deferred_install = Event('install').deferred(MyCharm._on_start)
```

## relation events:

```python
foo_relation = Relation('foo')
deferred_relation_changed_evt = foo_relation.changed_event.deferred(handler=MyCharm._on_foo_relation_changed)
```

On the output side, you can verify that an event that you expect to have been deferred during this trigger, has indeed
been deferred.

```python
from scenario import State, Context


class MyCharm(...):
    ...

    def _on_start(self, e):
        e.defer()


def test_defer(MyCharm):
    out = Context(MyCharm).run('start', State())
    assert len(out.deferred) == 1
    assert out.deferred[0].name == 'start'
```

## Deferring relation events

If you want to test relation event deferrals, some extra care needs to be taken. RelationEvents hold references to the
Relation instance they are about. So do they in Scenario. You can use the deferred helper to generate the data
structure:

```python
from scenario import State, Relation, deferred


class MyCharm(...):
    ...

    def _on_foo_relation_changed(self, e):
        e.defer()


def test_start_on_deferred_update_status(MyCharm):
    foo_relation = Relation('foo')
    State(
        relations=[foo_relation],
        deferred=[
            deferred('foo_relation_changed',
                     handler=MyCharm._on_foo_relation_changed,
                     relation=foo_relation)
        ]
    )
```

but you can also use a shortcut from the relation event itself, as mentioned above:

```python

from scenario import Relation


class MyCharm(...):
    ...


foo_relation = Relation('foo')
foo_relation.changed_event.deferred(handler=MyCharm._on_foo_relation_changed)
```

### Fine-tuning

The deferred helper Scenario provides will not support out of the box all custom event subclasses, or events emitted by
charm libraries or objects other than the main charm class.

For general-purpose usage, you will need to instantiate DeferredEvent directly.

```python
from scenario import DeferredEvent

my_deferred_event = DeferredEvent(
    handle_path='MyCharm/MyCharmLib/on/database_ready[1]',
    owner='MyCharmLib',  # the object observing the event. Could also be MyCharm.
    observer='_on_database_ready'
)
```

# StoredState

Scenario can simulate StoredState. You can define it on the input side as:

```python
from ops.charm import CharmBase
from ops.framework import StoredState as Ops_StoredState, Framework
from scenario import State, StoredState


class MyCharmType(CharmBase):
    my_stored_state = Ops_StoredState()

    def __init__(self, framework: Framework):
        super().__init__(framework)
        assert self.my_stored_state.foo == 'bar'  # this will pass!


state = State(stored_state=[
    StoredState(
        owner_path="MyCharmType",
        name="my_stored_state",
        content={
            'foo': 'bar',
            'baz': {42: 42},
        })
])
```

And the charm's runtime will see `self.stored_State.foo` and `.baz` as expected. Also, you can run assertions on it on
the output side the same as any other bit of state.

# Emitted events

If your charm deals with deferred events, custom events, and charm libs that in turn emit their own custom events, it
can be hard to examine the resulting control flow. In these situations it can be useful to verify that, as a result of a
given juju event triggering (say, 'start'), a specific chain of deferred and custom events is emitted on the charm. The
resulting state, black-box as it is, gives little insight into how exactly it was obtained.

`scenario`, among many other great things, is also a pytest plugin. It exposes a fixture called `emitted_events` that
you can use like so:

```python
from scenario import Context
from ops.charm import StartEvent


def test_foo(emitted_events):
    Context(...).run('start', ...)

    assert len(emitted_events) == 1
    assert isinstance(emitted_events[0], StartEvent)
```

## Customizing: capture_events

If you need more control over what events are captured (or you're not into pytest), you can use directly the context
manager that powers the `emitted_events` fixture: `scenario.capture_events`.
This context manager allows you to intercept any events emitted by the framework.

Usage:

```python
from ops.charm import StartEvent, UpdateStatusEvent
from scenario import State, Context, DeferredEvent, capture_events

with capture_events() as emitted:
    ctx = Context(...)
    state_out = ctx.run(
        "update-status",
        State(deferred=[DeferredEvent("start", ...)])
    )

# deferred events get reemitted first
assert isinstance(emitted[0], StartEvent)
# the main juju event gets emitted next
assert isinstance(emitted[1], UpdateStatusEvent)
# possibly followed by a tail of all custom events that the main juju event triggered in turn
# assert isinstance(emitted[2], MyFooEvent)
# ...
```

You can filter events by type like so:

```python
from ops.charm import StartEvent, RelationEvent
from scenario import capture_events

with capture_events(StartEvent, RelationEvent) as emitted:
    # capture all `start` and `*-relation-*` events.
    pass
```

Passing no event types, like: `capture_events()`, is equivalent to `capture_events(EventBase)`.

By default, **framework events** (`PreCommit`, `Commit`) are not considered for inclusion in the output list even if
they match the instance check. You can toggle that by passing: `capture_events(include_framework=True)`.

By default, **deferred events** are included in the listing if they match the instance check. You can toggle that by
passing: `capture_events(include_deferred=True)`.

# The virtual charm root

Before executing the charm, Scenario writes the metadata, config, and actions `yaml`s to a temporary directory. The
charm will see that tempdir as its 'root'. This allows us to keep things simple when dealing with metadata that can be
either inferred from the charm type being passed to `Context` or be passed to it as an argument, thereby overriding
the inferred one. This also allows you to test with charms defined on the fly, as in:

```python
from ops.charm import CharmBase
from scenario import State, Context


class MyCharmType(CharmBase):
    pass


ctx = Context(charm_type=MyCharmType,
              meta={'name': 'my-charm-name'})
ctx.run('start', State())
```

A consequence of this fact is that you have no direct control over the tempdir that we are creating to put the metadata
you are passing to trigger (because `ops` expects it to be a file...). That is, unless you pass your own:

```python
from ops.charm import CharmBase
from scenario import State, Context
import tempfile


class MyCharmType(CharmBase):
    pass


td = tempfile.TemporaryDirectory()
state = Context(
    charm_type=MyCharmType,
    meta={'name': 'my-charm-name'},
    charm_root=td.name
).run('start', State())
```

Do this, and you will be able to set up said directory as you like before the charm is run, as well as verify its
contents after the charm has run. Do keep in mind that the metadata files will be overwritten by Scenario, and therefore
ignored.

# Consistency checks

A Scenario, that is, the combination of an event, a state, and a charm, is consistent if it's plausible in JujuLand. For
example, Juju can't emit a `foo-relation-changed` event on your charm unless your charm has declared a `foo` relation
endpoint in its `metadata.yaml`. If that happens, that's a juju bug. Scenario however assumes that Juju is bug-free,
therefore, so far as we're concerned, that can't happen, and therefore we help you verify that the scenarios you create
are consistent and raise an exception if that isn't so.

That happens automatically behind the scenes whenever you trigger an event;
`scenario.consistency_checker.check_consistency` is called and verifies that the scenario makes sense.

## Caveats:

- False positives: not all checks are implemented yet; more will come.
- False negatives: it is possible that a scenario you know to be consistent is seen as inconsistent. That is probably a
  bug in the consistency checker itself, please report it.
- Inherent limitations: if you have a custom event whose name conflicts with a builtin one, the consistency constraints
  of the builtin one will apply. For example: if you decide to name your custom event `bar-pebble-ready`, but you are
  working on a machine charm or don't have either way a `bar` container in your `metadata.yaml`, Scenario will flag that
  as inconsistent.

## Bypassing the checker

If you have a clear false negative, are explicitly testing 'edge', inconsistent situations, or for whatever reason the
checker is in your way, you can set the `SCENARIO_SKIP_CONSISTENCY_CHECKS` envvar and skip it altogether. Hopefully you
don't need that.

# Charm State

Suppose that your charm code makes an http call to a server somewhere to get some data, say, the current temperature reading from a sensor on top of the Nieuwe Kerk in Delft, The Netherlands. 

If you follow the best practices of how to structure your charm code, then you are aware that this piece of data, at runtime, is categorised as 'charm state'. 
Scenario offers a way to plug into this system natively, and integrate this charm state data structure into its own `State` tree.

If your charm code looks like this:
```python
from dataclasses import dataclass
from ops import CharmBase, Framework

from scenario.charm_state import CharmStateBackend
from scenario.state import CharmState

# in state.py
@dataclass(frozen=True)
class MyState(CharmState):
    temperature: float = 4.5  # brr

# in state.py
class MyCharmStateBackend(CharmStateBackend):
    @property
    def temperature(self) -> int:
        import requests
        return requests.get('http://nieuwekerk.delft.nl/temp...').json()['celsius']
    
    # no setter: you can't change the weather. 
    # ... Can you?
    
# in charm.py
class MyCharm(CharmBase):
    state = MyCharmStateBackend()

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.temperature = self.state.temperature
```

Then you can write scenario tests like that:

```python
import pytest
from scenario import Context, State
from charm import MyCharm
from state import MyState

@pytest.fixture
def ctx():
    return Context(MyCharm, meta={"name": "foo"})


@pytest.mark.parametrize("temp", (1.1, 10.2, 20.3))
def test_get(ctx, temp):
    state = State(charm_state=MyState("state", temperature=temp))

    # the charm code will get the value from State.charm_state.temperature instead of making http calls at test-time.
    def post_event(charm: MyCharm):
        assert charm.temperature == temp

    ctx.run("start", state=state, post_event=post_event)
```


# Snapshot

Scenario comes with a cli tool called `snapshot`. Assuming you've pip-installed `ops-scenario`, you should be able to
reach the entry point by typing `scenario snapshot` in a shell so long as the install dir is in your `PATH`.

Snapshot's purpose is to gather the `State` data structure from a real, live charm running in some cloud your local juju
client has access to. This is handy in case:

- you want to write a test about the state the charm you're developing is currently in
- your charm is bork or in some inconsistent state, and you want to write a test to check the charm will handle it
  correctly the next time around (aka regression testing)
- you are new to Scenario and want to quickly get started with a real-life example.

Suppose you have a Juju model with a `prometheus-k8s` unit deployed as `prometheus-k8s/0`. If you type
`scenario snapshot prometheus-k8s/0`, you will get a printout of the State object. Pipe that out into some file, import
all you need from `scenario`, and you have a working `State` that you can `Context.run` events with.

You can also pass a `--format` flag to obtain instead:

- a jsonified `State` data structure, for portability
- a full-fledged pytest test case (with imports and all), where you only have to fill in the charm type and the event
  that you wish to trigger.

