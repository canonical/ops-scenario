# Upgrading

## Scenario 6.x to Scenario 7.x

Scenario 7.0 has substantial API incompatibility with earlier versions, but
comes with an intention to reduce the frequency of breaking changes in the
future, aligning with the `ops` library.

The changes listed below are not the only features introduced in Scenario 7.0
(for that, see the release notes), but cover the breaking changes where you will
need to update your existing Scenario tests.

### Specify events via context.on

In previous versions of Scenario, an event would be passed to `Context.run`
as a string name, via a convenient shorthand property of a state component
(e.g. `Relation`, `Container`), or by explicitly constructing an `Event` object.
These have been unified into a single `Context.on.{event name}()` approach,
which is more consistent, resembles the structure you're familiar with from
charm `observe` calls, and should provide more context to IDE and linting tools.

```python
# Older Scenario code.
ctx.run('start', state)
ctx.run(container.pebble_ready_event, state)
ctx.run(Event('relation-joined', relation=relation), state)

# Scenario 7.x
ctx.run(ctx.on.start(), state)
ctx.run(ctx.on.pebble_ready(container=container), state)
ctx.run(ctx.on.relation_joined(relation=relation), state)
```

### State components are (frozen) sets

Like containers, relations, and networks, state components do not have any
inherent ordering. When these were lists, 'magic' numbers tended to creep into
test code. These are now all sets, and have 'get' methods to retrieve the
object you want to assert on. In addition, they are actually `frozenset`s
(Scenario will automatically freeze them if you pass a `set`), which increases
the immutability of the state and prevents accidentally modifying the input
state.

```python
# Older Scenario code.
state_in = State(containers=[c1, c2], relations=[r1, r2])
...
assert state_out.containers[1]...
assert state_out.relations[0]...
state_out.relations.append(r3)  # Not recommended!

# Scenario 7.x
state_in = State(containers={c1, c2}, relations={r1, r2})
...
assert state_out.get_container(c2.name)...
assert state_out.get_relation(id=r1.id)...
new_state = dataclasses.replace(state_out, relations=state_out.relations + {r3})
```

### State components are passed by keyword

Most state components, and the `State` object itself, now request at least some
arguments to be passed by keyword. In most cases, it's likely that you were
already doing this, but the API is now enforced.

```python
# Older Scenario code.
container1 = Container('foo', True)
state = State({'key': 'value'}, [relation1, relation2], [network], [container1, container2])

# Scenario 7.x
container1 = Container('foo', can_connect=True)
state = State(
    config={'key': 'value'},
    relations={relation1, relation2},
    networks={network},
    containers={container1, container2},
)
```

### Trigger custom events by triggering the underlying Juju event

Scenario no longer supports explicitly running custom events. Instead, you
should run the Juju event(s) that will trigger the custom event. For example,
if you have a charm lib that will emit a `database-created` event on
`relation-created`:

```python
# Older Scenario code.
ctx.run("my_charm_lib.on.database_created", state)

# Scenario 7.x
ctx.run(ctx.on.relation_created(relation=relation), state)
```

Scenario will still capture custom events in `Context.emitted_events`.

### Copy objects with dataclasses.replace and copy.deepcopy

The `copy()` and `replace()` methods of `State` and the various state components
have been removed. You should use the `dataclasses.replace` and `copy.deepcopy`
methods instead.

```python
# Older Scenario code.
new_container = container.replace(can_connect=True)
duplicate_relation = relation.copy()

# Scenario 7.x
new_container = dataclasses.replace(container, can_connect=True)
duplicate_relation = copy.deepcopy(relation)
```

### Define resources with the Resource class

The resources in State objects were previously plain dictionaryes, and are now
`scenario.Resource` objects, aligning with all of the other State components.

```python
# Older Scenario code
state = State(resources={"/path/to/foo", pathlib.Path("/mock/foo")})

# Scenario 7.x
resource = Resource(location="/path/to/foo", source=pathlib.Path("/mock/foo"))
state = State(resources={resource})
```

### Give Network objects a binding name attribute

Previously, `Network` objects were added to the state as a dictionary of
`{binding_name: network}`. Now, `Network` objects are added to the state as a
set, like the other components. This means that the `Network` object now
requires a binding name to be passed in when it is created.

```python
# Older Scenario code
state = State(networks={"foo": Network.default()})

# Scenario 7.x
state = State(networks={Network.default("foo")})
```

### Update names: State.storages, State.stored_states, Container.execs, Container.service_statuses

The `State.storage` and `State.stored_state` attributes are now plurals. This
reflects that you may have more than one in the state, and also aligns with the
other State components.

```python
# Older Scenario code
state = State(stored_state=[ss1, ss2], storage=[s1, s2])

# Scenario 7.x
state = State(stored_states={s1, s2}, storages={s1, s2})
```

Similarly, `Container.exec_mocks` is now named `Container.execs`,
`Container.service_status` is now named `Container.service_statuses`, and
`ExecOutput` is now named `Exec`.

```python
# Older Scenario code
container = Container(
    name="foo",
    exec_mock={("ls", "-ll"): ExecOutput(return_code=0, stdout=....)},
    service_status={"srv1": ops.pebble.ServiceStatus.ACTIVE}
)

# Scenario 7.x
container = Container(
    name="foo",
    execs={Exec(("ls", "-ll"), return_code=0, stdout=....)},
    service_statuses={"srv1": ops.pebble.ServiceStatus.ACTIVE},
)
```

### Don't use `RelationBase`, `Event`, or `StoredState.data_type_name`

Several attributes and classes that were never intended for end users have been
made private:

* The `data_type_name` attribute of `StoredState` is now private.
* The `RelationBase` class is now private.
* The `Event` class is now private.

### Catan replaces `scenario.sequences`

The `scenario.sequences` module has been removed. We encourage you to look at
the new [Catan](https://github.com/PietroPasotti/catan) package.

### Use the jsonpatch library directly

The `State.jsonpatch_delta()` and `state.sort_patch()` methods have been
removed. We are considering adding delta-comparisons of state again in the
future, but have not yet decided how this will look. In the meantime, you can
use the jsonpatch package directly if necessary. See the tests/helpers.py file
for an example.

### No need to call `cleanup`/`clear`

The `Context.cleanup()` and `Context.clear()` methods have been removed. You
do not need to manually call any cleanup methods after running an event. If you
want a fresh `Context` (e.g. with no history), you should create a new object.

### Use the context handler rather than `pre_event` and `post_event`

The deprecated `pre_event` and `post_event` arguments to `run` and `run_action`
have been removed. Use the appropriate context handler instead.

TODO: name the context handler above, once we decide on manager/event/event_manager/context-as-manager, etc.

### Only include secrets in the state if the charm has permission to view them

`Secret.granted` has been removed. Only include in the state the secrets that
the charm has permission to (at least) view.

### Use 'app' for application-owned secrets

`Secret.owner` should be `'app'` (or `'unit'` or `None`) rather than
`'application'`.

### Compare statuses with status objects

It is no longer possible to compare statuses with tuples. Create the appropriate
status object and compare to that. Note that you should always compare statuses
with `==` not `is`.

### Pass the name of the container to `State.get_container`

The `State.get_container` method previously allowed passing in a `Container`
object or a container name, but now only accepts a name. This is more consistent
with the other new `get_*` methods, some of which would be quite complex if they
accepted an object or key.

### Use `State.storages` to get all the storages in the state

The `State.get_storages` method has been removed. This was primarily intended
for internal use. You can use `State.get_storage` or iterate through
`State.storages` instead.
