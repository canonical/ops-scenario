#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, Union, cast

from ops import CharmBase, EventBase

from scenario.logger import logger as scenario_logger
from scenario.runtime import Runtime
from scenario.state import (
    Container,
    MetadataNotFoundError,
    Secret,
    Storage,
    _Action,
    _CharmSpec,
    _Event,
    _max_posargs,
)

if TYPE_CHECKING:  # pragma: no cover
    from ops.testing import CharmType

    from scenario.ops_main_mock import Ops
    from scenario.state import AnyJson, AnyRelation, JujuLogLine, State, _EntityStatus

    PathLike = Union[str, Path]

logger = scenario_logger.getChild("runtime")

DEFAULT_JUJU_VERSION = "3.4"


@dataclasses.dataclass(frozen=True)
class ActionOutput(_max_posargs(0)):
    """Wraps the results of running an action event with ``run_action``."""

    state: "State"
    """The charm state after the action has been handled.

    In most cases, actions are not expected to be affecting it."""
    logs: List[str]
    """Any logs associated with the action output, set by the charm with
    :meth:`ops.ActionEvent.log`."""
    results: Optional[Dict[str, Any]] = None
    """Key-value mapping assigned by the charm as a result of the action.
    Will be None if the charm never calls :meth:`ops.ActionEvent.set_results`."""
    failure: Optional[str] = None
    """None if the action was successful, otherwise the message the charm set with
    :meth:`ops.ActionEvent.fail`."""

    @property
    def success(self) -> bool:
        """True if this action was a success, False otherwise."""
        return self.failure is None


class InvalidEventError(RuntimeError):
    """raised when something is wrong with the event passed to Context.run_*"""


class InvalidActionError(InvalidEventError):
    """raised when something is wrong with the action passed to Context.run_action"""


class ContextSetupError(RuntimeError):
    """Raised by Context when setup fails."""


class AlreadyEmittedError(RuntimeError):
    """Raised when ``run()`` is called more than once."""


class _Manager:
    """Context manager to offer test code some runtime charm object introspection."""

    def __init__(
        self,
        ctx: "Context",
        arg: Union[str, _Action, _Event],
        state_in: "State",
    ):
        self._ctx = ctx
        self._arg = arg
        self._state_in = state_in

        self._emitted: bool = False
        self._run = None

        self.ops: Optional["Ops"] = None
        self.output: Optional[Union["State", ActionOutput]] = None

    @property
    def charm(self) -> CharmBase:
        if not self.ops:
            raise RuntimeError(
                "you should __enter__ this contextmanager before accessing this",
            )
        return cast(CharmBase, self.ops.charm)

    @property
    def _runner(self):
        raise NotImplementedError("override in subclass")

    def _get_output(self):
        raise NotImplementedError("override in subclass")

    def __enter__(self):
        self._wrapped_ctx = wrapped_ctx = self._runner(self._arg, self._state_in)
        ops = wrapped_ctx.__enter__()
        self.ops = ops
        return self

    def run(self) -> Union[ActionOutput, "State"]:
        """Emit the event and proceed with charm execution.

        This can only be done once.
        """
        if self._emitted:
            raise AlreadyEmittedError("Can only context.manager.run() once.")
        self._emitted = True

        # wrap up Runtime.exec() so that we can gather the output state
        self._wrapped_ctx.__exit__(None, None, None)

        self.output = out = self._get_output()
        return out

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: U100
        if not self._emitted:
            logger.debug("manager not invoked. Doing so implicitly...")
            self.run()


class _EventManager(_Manager):
    if TYPE_CHECKING:  # pragma: no cover
        output: State  # pyright: ignore[reportIncompatibleVariableOverride]

        def run(self) -> "State":
            return cast("State", super().run())

    @property
    def _runner(self):
        return self._ctx._run_event  # noqa

    def _get_output(self):
        return self._ctx._output_state  # noqa


class _ActionManager(_Manager):
    if TYPE_CHECKING:  # pragma: no cover
        output: ActionOutput  # pyright: ignore[reportIncompatibleVariableOverride]

        def run(self) -> "ActionOutput":
            return cast("ActionOutput", super().run())

    @property
    def _runner(self):
        return self._ctx._run  # noqa

    def _get_output(self):
        return self._ctx._finalize_action(self._ctx.output_state)  # noqa


class _CharmEvents:
    """Events generated by Juju pertaining to application lifecycle.

    By default, the events listed as attributes of this class will be
    provided via the :attr:`Context.on` attribute. For example::

        ctx.run(ctx.on.config_changed(), state)

    This behaves similarly to the :class:`ops.CharmEvents` class but is much
    simpler as there are no dynamically named attributes, and no __getattr__
    version to get events. In addition, all of the attributes are methods,
    which are used to connect the event to the specific container object that
    they relate to (or, for simpler events like "start" or "stop", take no
    arguments).
    """

    @staticmethod
    def install():
        return _Event("install")

    @staticmethod
    def start():
        return _Event("start")

    @staticmethod
    def stop():
        return _Event("stop")

    @staticmethod
    def remove():
        return _Event("remove")

    @staticmethod
    def update_status():
        return _Event("update_status")

    @staticmethod
    def config_changed():
        return _Event("config_changed")

    @staticmethod
    def upgrade_charm():
        return _Event("upgrade_charm")

    @staticmethod
    def pre_series_upgrade():
        return _Event("pre_series_upgrade")

    @staticmethod
    def post_series_upgrade():
        return _Event("post_series_upgrade")

    @staticmethod
    def leader_elected():
        return _Event("leader_elected")

    @staticmethod
    def secret_changed(secret: Secret):
        if secret.owner:
            raise ValueError(
                "This unit will never receive secret-changed for a secret it owns.",
            )
        return _Event("secret_changed", secret=secret)

    @staticmethod
    def secret_expired(secret: Secret, *, revision: int):
        if not secret.owner:
            raise ValueError(
                "This unit will never receive secret-expire for a secret it does not own.",
            )
        return _Event("secret_expired", secret=secret, secret_revision=revision)

    @staticmethod
    def secret_rotate(secret: Secret):
        if not secret.owner:
            raise ValueError(
                "This unit will never receive secret-rotate for a secret it does not own.",
            )
        return _Event("secret_rotate", secret=secret)

    @staticmethod
    def secret_remove(secret: Secret, *, revision: int):
        if not secret.owner:
            raise ValueError(
                "This unit will never receive secret-removed for a secret it does not own.",
            )
        return _Event("secret_remove", secret=secret, secret_revision=revision)

    @staticmethod
    def collect_app_status():
        return _Event("collect_app_status")

    @staticmethod
    def collect_unit_status():
        return _Event("collect_unit_status")

    @staticmethod
    def relation_created(relation: "AnyRelation"):
        return _Event(f"{relation.endpoint}_relation_created", relation=relation)

    @staticmethod
    def relation_joined(relation: "AnyRelation", *, remote_unit: Optional[int] = None):
        return _Event(
            f"{relation.endpoint}_relation_joined",
            relation=relation,
            relation_remote_unit_id=remote_unit,
        )

    @staticmethod
    def relation_changed(relation: "AnyRelation", *, remote_unit: Optional[int] = None):
        return _Event(
            f"{relation.endpoint}_relation_changed",
            relation=relation,
            relation_remote_unit_id=remote_unit,
        )

    @staticmethod
    def relation_departed(
        relation: "AnyRelation",
        *,
        remote_unit: Optional[int] = None,
        departing_unit: Optional[int] = None,
    ):
        return _Event(
            f"{relation.endpoint}_relation_departed",
            relation=relation,
            relation_remote_unit_id=remote_unit,
            relation_departed_unit_id=departing_unit,
        )

    @staticmethod
    def relation_broken(relation: "AnyRelation"):
        return _Event(f"{relation.endpoint}_relation_broken", relation=relation)

    @staticmethod
    def storage_attached(storage: Storage):
        return _Event(f"{storage.name}_storage_attached", storage=storage)

    @staticmethod
    def storage_detaching(storage: Storage):
        return _Event(f"{storage.name}_storage_detaching", storage=storage)

    @staticmethod
    def pebble_ready(container: Container):
        return _Event(f"{container.name}_pebble_ready", container=container)

    @staticmethod
    def action(
        name: str,
        params: Optional[Dict[str, "AnyJson"]] = None,
        id: Optional[str] = None,
    ):
        kwargs = {}
        if params:
            kwargs["params"] = params
        if id:
            kwargs["id"] = id
        return _Event(f"{name}_action", action=_Action(name, **kwargs))


class Context:
    """Represents a simulated charm's execution context.

    It is the main entry point to running a scenario test.

    It contains: the charm source code being executed, the metadata files associated with it,
    a charm project repository root, and the Juju version to be simulated.

    After you have instantiated ``Context``, typically you will call one of ``run()`` or
    ``run_action()`` to execute the charm once, write any assertions you like on the output
    state returned by the call, write any assertions you like on the ``Context`` attributes,
    then discard the ``Context``.

    Each ``Context`` instance is in principle designed to be single-use:
    ``Context`` is not cleaned up automatically between charm runs.
    You can call ``.clear()`` to do some clean up, but we don't guarantee all state will be gone.

    Any side effects generated by executing the charm, that are not rightful part of the
    ``State``, are in fact stored in the ``Context``:

    - :attr:`juju_log`: record of what the charm has sent to juju-log
    - :attr:`app_status_history`: record of the app statuses the charm has set
    - :attr:`unit_status_history`: record of the unit statuses the charm has set
    - :attr:`workload_version_history`: record of the workload versions the charm has set
    - :attr:`emitted_events`: record of the events (including custom) that the charm has processed

    This allows you to write assertions not only on the output state, but also, to some
    extent, on the path the charm took to get there.

    A typical scenario test will look like::

        from scenario import Context, State
        from ops import ActiveStatus
        from charm import MyCharm, MyCustomEvent  # noqa

        def test_foo():
            # Arrange: set the context up
            c = Context(MyCharm)
            # Act: prepare the state and emit an event
            state_out = c.run('update-status', State())
            # Assert: verify the output state is what you think it should be
            assert state_out.unit_status == ActiveStatus('foobar')
            # Assert: verify the Context contains what you think it should
            assert len(c.emitted_events) == 4
            assert isinstance(c.emitted_events[3], MyCustomEvent)

    If the charm, say, expects a ``./src/foo/bar.yaml`` file present relative to the
    execution cwd, you need to use the ``charm_root`` argument. For example::

        import scenario
        import tempfile
        virtual_root = tempfile.TemporaryDirectory()
        local_path = Path(local_path.name)
        (local_path / 'foo').mkdir()
        (local_path / 'foo' / 'bar.yaml').write_text('foo: bar')
        scenario.Context(... charm_root=virtual_root).run(...)

    Args:
        charm_type: the CharmBase subclass to call :meth:`ops.main` on.
        meta: charm metadata to use. Needs to be a valid metadata.yaml format (as a dict).
            If none is provided, we will search for a ``metadata.yaml`` file in the charm root.
        actions: charm actions to use. Needs to be a valid actions.yaml format (as a dict).
            If none is provided, we will search for a ``actions.yaml`` file in the charm root.
        config: charm config to use. Needs to be a valid config.yaml format (as a dict).
            If none is provided, we will search for a ``config.yaml`` file in the charm root.
        juju_version: Juju agent version to simulate.
        app_name: App name that this charm is deployed as. Defaults to the charm name as
            defined in its metadata
        unit_id: Unit ID that this charm is deployed as. Defaults to 0.
        charm_root: virtual charm root the charm will be executed with.
    """

    def __init__(
        self,
        charm_type: Type["CharmType"],
        meta: Optional[Dict[str, Any]] = None,
        *,
        actions: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        charm_root: Optional["PathLike"] = None,
        juju_version: str = DEFAULT_JUJU_VERSION,
        capture_deferred_events: bool = False,
        capture_framework_events: bool = False,
        app_name: Optional[str] = None,
        unit_id: Optional[int] = 0,
        app_trusted: bool = False,
    ):
        """Represents a simulated charm's execution context.

        It is the main entry point to running a scenario test.

        It contains: the charm source code being executed, the metadata files associated with it,
        a charm project repository root, and the juju version to be simulated.

        After you have instantiated Context, typically you will call one of `run()` or
        `run_action()` to execute the charm once, write any assertions you like on the output
        state returned by the call, write any assertions you like on the Context attributes,
        then discard the Context.
        Each Context instance is in principle designed to be single-use:
        Context is not cleaned up automatically between charm runs.
        You can call `.clear()` to do some clean up, but we don't guarantee all state will be gone.

        Any side effects generated by executing the charm, that are not rightful part of the State,
        are in fact stored in the Context:
        - ``juju_log``: record of what the charm has sent to juju-log
        - ``app_status_history``: record of the app statuses the charm has set
        - ``unit_status_history``: record of the unit statuses the charm has set
        - ``workload_version_history``: record of the workload versions the charm has set
        - ``emitted_events``: record of the events (including custom ones) that the charm has
            processed

        This allows you to write assertions not only on the output state, but also, to some
        extent, on the path the charm took to get there.

        A typical scenario test will look like:

        >>> from scenario import Context, State
        >>> from ops import ActiveStatus
        >>> from charm import MyCharm, MyCustomEvent  # noqa
        >>>
        >>> def test_foo():
        >>>     # Arrange: set the context up
        >>>     c = Context(MyCharm)
        >>>     # Act: prepare the state and emit an event
        >>>     state_out = c.run(c.update_status(), State())
        >>>     # Assert: verify the output state is what you think it should be
        >>>     assert state_out.unit_status == ActiveStatus('foobar')
        >>>     # Assert: verify the Context contains what you think it should
        >>>     assert len(c.emitted_events) == 4
        >>>     assert isinstance(c.emitted_events[3], MyCustomEvent)

        :arg charm_type: the CharmBase subclass to call ``ops.main()`` on.
        :arg meta: charm metadata to use. Needs to be a valid metadata.yaml format (as a dict).
            If none is provided, we will search for a ``metadata.yaml`` file in the charm root.
        :arg actions: charm actions to use. Needs to be a valid actions.yaml format (as a dict).
            If none is provided, we will search for a ``actions.yaml`` file in the charm root.
        :arg config: charm config to use. Needs to be a valid config.yaml format (as a dict).
            If none is provided, we will search for a ``config.yaml`` file in the charm root.
        :arg juju_version: Juju agent version to simulate.
        :arg app_name: App name that this charm is deployed as. Defaults to the charm name as
            defined in metadata.yaml.
        :arg unit_id: Unit ID that this charm is deployed as. Defaults to 0.
        :arg app_trusted: whether the charm has Juju trust (deployed with ``--trust`` or added with
            ``juju trust``). Defaults to False.
        :arg charm_root: virtual charm root the charm will be executed with.
            If the charm, say, expects a `./src/foo/bar.yaml` file present relative to the
            execution cwd, you need to use this. E.g.:

            >>> import scenario
            >>> import tempfile
            >>> virtual_root = tempfile.TemporaryDirectory()
            >>> local_path = Path(local_path.name)
            >>> (local_path / 'foo').mkdir()
            >>> (local_path / 'foo' / 'bar.yaml').write_text('foo: bar')
            >>> scenario.Context(... charm_root=virtual_root).run(...)
        """

        if not any((meta, actions, config)):
            logger.debug("Autoloading charmspec...")
            try:
                spec = _CharmSpec.autoload(charm_type)
            except MetadataNotFoundError as e:
                raise ContextSetupError(
                    f"Cannot setup scenario with `charm_type`={charm_type}. "
                    f"Did you forget to pass `meta` to this Context?",
                ) from e

        else:
            if not meta:
                meta = {"name": str(charm_type.__name__)}
            spec = _CharmSpec(
                charm_type=charm_type,
                meta=meta,
                actions=actions,
                config=config,
            )

        self.charm_spec = spec
        self.charm_root = charm_root
        self.juju_version = juju_version
        if juju_version.split(".")[0] == "2":
            logger.warn(
                "Juju 2.x is closed and unsupported. You may encounter inconsistencies.",
            )

        self._app_name = app_name
        self._unit_id = unit_id
        self.app_trusted = app_trusted
        self._tmp = tempfile.TemporaryDirectory()

        # config for what events to be captured in emitted_events.
        self.capture_deferred_events = capture_deferred_events
        self.capture_framework_events = capture_framework_events

        # streaming side effects from running an event
        self.juju_log: List["JujuLogLine"] = []
        self.app_status_history: List["_EntityStatus"] = []
        self.unit_status_history: List["_EntityStatus"] = []
        self.workload_version_history: List[str] = []
        self.emitted_events: List[EventBase] = []
        self.requested_storages: Dict[str, int] = {}

        # set by Runtime.exec() in self._run()
        self._output_state: Optional["State"] = None

        # ephemeral side effects from running an action

        self._action_logs: List[str] = []
        self._action_results: Optional[Dict[str, str]] = None
        self._action_failure: Optional[str] = None

        self.on = _CharmEvents()

    def _set_output_state(self, output_state: "State"):
        """Hook for Runtime to set the output state."""
        self._output_state = output_state

    @property
    def output_state(self) -> "State":
        """The output state obtained by running an event on this context.

        Raises:
            RuntimeError: if this ``Context`` hasn't been :meth:`run` yet.
        """
        if not self._output_state:
            raise RuntimeError(
                "No output state available. ``.run()`` this Context first.",
            )
        return self._output_state

    def _get_container_root(self, container_name: str):
        """Get the path to a tempdir where this container's simulated root will live."""
        return Path(self._tmp.name) / "containers" / container_name

    def _get_storage_root(self, name: str, index: int) -> Path:
        """Get the path to a tempdir where this storage's simulated root will live."""
        storage_root = Path(self._tmp.name) / "storages" / f"{name}-{index}"
        # in the case of _get_container_root, _MockPebbleClient will ensure the dir exists.
        storage_root.mkdir(parents=True, exist_ok=True)
        return storage_root

    def _record_status(self, state: "State", is_app: bool):
        """Record the previous status before a status change."""
        if is_app:
            self.app_status_history.append(state.app_status)
        else:
            self.unit_status_history.append(state.unit_status)

    def manager(self, event: "_Event", state: "State"):
        """Context manager to introspect live charm object before and after the event is emitted.

        Usage::

            ctx = Context(MyCharm)
            with ctx.manager(ctx.on.start(), State()) as manager:
                assert manager.charm._some_private_attribute == "foo"  # noqa
                manager.run()  # this will fire the event
                assert manager.charm._some_private_attribute == "bar"  # noqa

        Args:
            event: the :class:`Event` that the charm will respond to.
            state: the :class:`State` instance to use when handling the Event.
        """
        return _EventManager(self, event, state)

    def action_manager(self, action: "_Action", state: "State"):
        """Context manager to introspect live charm object before and after the event is emitted.

        Usage:
        >>> with Context().action_manager(Action("foo"), State()) as manager:
        >>>     assert manager.charm._some_private_attribute == "foo"  # noqa
        >>>     manager.run()  # this will fire the event
        >>>     assert manager.charm._some_private_attribute == "bar"  # noqa

        :arg action: the Action that the charm will execute.
        :arg state: the State instance to use as data source for the hook tool calls that the
            charm will invoke when handling the Action (event).
        """
        return _ActionManager(self, action, state)

    @contextmanager
    def _run_event(self, event: "_Event", state: "State"):
        with self._run(event=event, state=state) as ops:
            yield ops

    def run(self, event: "_Event", state: "State") -> "State":
        """Trigger a charm execution with an Event and a State.

        Calling this function will call ``ops.main`` and set up the context according to the
        specified ``State``, then emit the event on the charm.

        :arg event: the Event that the charm will respond to.
        :arg state: the State instance to use as data source for the hook tool calls that the
            charm will invoke when handling the Event.
        """
        if isinstance(event, _Action) or event.action:
            raise InvalidEventError("Use run_action() to run an action event.")
        with self._run_event(event=event, state=state) as ops:
            ops.emit()
        return self.output_state

    def run_action(self, event: "_Event", state: "State") -> ActionOutput:
        """Trigger a charm execution with an action event and a State.

        Calling this function will call ``ops.main`` and set up the context according to the
        specified ``State``, then emit the event on the charm.

        :arg event: the action event that the charm will execute.
        :arg state: the State instance to use as data source for the hook tool calls that the
            charm will invoke when handling the action event.
        """
        with self._run(event=event, state=state) as ops:
            ops.emit()
        return self._finalize_action(self.output_state)

    def _finalize_action(self, state_out: "State"):
        ao = ActionOutput(
            state=state_out,
            logs=self._action_logs,
            results=self._action_results,
            failure=self._action_failure,
        )

        # reset all action-related state
        self._action_logs = []
        self._action_results = None
        self._action_failure = None

        return ao

    @contextmanager
    def _run(self, event: "_Event", state: "State"):
        runtime = Runtime(
            charm_spec=self.charm_spec,
            juju_version=self.juju_version,
            charm_root=self.charm_root,
            app_name=self._app_name,
            unit_id=self._unit_id,
        )
        with runtime.exec(
            state=state,
            event=event,
            context=self,
        ) as ops:
            yield ops
