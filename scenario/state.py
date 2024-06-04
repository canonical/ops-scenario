#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import datetime
import inspect
import re
from collections import namedtuple
from enum import Enum
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generic,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)
from uuid import uuid4

import yaml
from ops import pebble
from ops.charm import CharmBase, CharmEvents
from ops.model import SecretRotate, StatusBase

from scenario.logger import logger as scenario_logger

JujuLogLine = namedtuple("JujuLogLine", ("level", "message"))

if TYPE_CHECKING:  # pragma: no cover
    from scenario import Context

    PathLike = Union[str, Path]
    AnyRelation = Union["Relation", "PeerRelation", "SubordinateRelation"]
    AnyJson = Union[str, bool, dict, int, float, list]
    RawSecretRevisionContents = RawDataBagContents = Dict[str, str]
    UnitID = int

CharmType = TypeVar("CharmType", bound=CharmBase)

logger = scenario_logger.getChild("state")

ATTACH_ALL_STORAGES = "ATTACH_ALL_STORAGES"
CREATE_ALL_RELATIONS = "CREATE_ALL_RELATIONS"
BREAK_ALL_RELATIONS = "BREAK_ALL_RELATIONS"
DETACH_ALL_STORAGES = "DETACH_ALL_STORAGES"

ACTION_EVENT_SUFFIX = "_action"
# all builtin events except secret events. They're special because they carry secret metadata.
BUILTIN_EVENTS = {
    "start",
    "stop",
    "install",
    "install",
    "start",
    "stop",
    "remove",
    "update_status",
    "config_changed",
    "upgrade_charm",
    "pre_series_upgrade",
    "post_series_upgrade",
    "leader_elected",
    "leader_settings_changed",
    "collect_metrics",
}
FRAMEWORK_EVENTS = {
    "pre_commit",
    "commit",
    "collect_app_status",
    "collect_unit_status",
}
PEBBLE_READY_EVENT_SUFFIX = "_pebble_ready"
RELATION_EVENTS_SUFFIX = {
    "_relation_changed",
    "_relation_broken",
    "_relation_joined",
    "_relation_departed",
    "_relation_created",
}
STORAGE_EVENTS_SUFFIX = {
    "_storage_detaching",
    "_storage_attached",
}

SECRET_EVENTS = {
    "secret_changed",
    "secret_removed",
    "secret_rotate",
    "secret_expired",
}

META_EVENTS = {
    "CREATE_ALL_RELATIONS": "_relation_created",
    "BREAK_ALL_RELATIONS": "_relation_broken",
    "DETACH_ALL_STORAGES": "_storage_detaching",
    "ATTACH_ALL_STORAGES": "_storage_attached",
}


class StateValidationError(RuntimeError):
    """Raised when individual parts of the State are inconsistent."""

    # as opposed to InconsistentScenario error where the
    # **combination** of several parts of the State are.


class MetadataNotFoundError(RuntimeError):
    """Raised when Scenario can't find a metadata.yaml file in the provided charm root."""


@dataclasses.dataclass(frozen=True)
class Secret:
    id: str
    # CAUTION: ops-created Secrets (via .add_secret()) will have a canonicalized
    #  secret id (`secret:` prefix)
    #  but user-created ones will not. Using post-init to patch it in feels bad, but requiring the user to
    #  add the prefix manually every time seems painful as well.

    # mapping from revision IDs to each revision's contents
    contents: Dict[int, "RawSecretRevisionContents"]

    # indicates if the secret is owned by THIS unit, THIS app or some other app/unit.
    # if None, the implication is that the secret has been granted to this unit.
    owner: Literal["unit", "app", None] = None

    # what revision is currently tracked by this charm. Only meaningful if owner=False
    revision: int = 0

    # mapping from relation IDs to remote unit/apps to which this secret has been granted.
    # Only applicable if owner
    remote_grants: Dict[int, Set[str]] = dataclasses.field(default_factory=dict)

    label: Optional[str] = None
    description: Optional[str] = None
    expire: Optional[datetime.datetime] = None
    rotate: Optional[SecretRotate] = None

    def _set_revision(self, revision: int):
        """Set a new tracked revision."""
        # bypass frozen dataclass
        object.__setattr__(self, "revision", revision)

    def _update_metadata(
        self,
        content: Optional["RawSecretRevisionContents"] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        expire: Optional[datetime.datetime] = None,
        rotate: Optional[SecretRotate] = None,
    ):
        """Update the metadata."""
        revision = max(self.contents.keys())
        if content:
            self.contents[revision + 1] = content

        # bypass frozen dataclass
        if label:
            object.__setattr__(self, "label", label)
        if description:
            object.__setattr__(self, "description", description)
        if expire:
            if isinstance(expire, datetime.timedelta):
                expire = datetime.datetime.now() + expire
            object.__setattr__(self, "expire", expire)
        if rotate:
            object.__setattr__(self, "rotate", rotate)


def normalize_name(s: str):
    """Event names, in Scenario, uniformly use underscores instead of dashes."""
    return s.replace("-", "_")


@dataclasses.dataclass(frozen=True)
class Address:
    hostname: str
    value: str
    cidr: str
    address: str = ""  # legacy


@dataclasses.dataclass(frozen=True)
class BindAddress:
    interface_name: str
    addresses: List[Address]
    mac_address: Optional[str] = None

    def hook_tool_output_fmt(self):
        # dumps itself to dict in the same format the hook tool would
        # todo support for legacy (deprecated) `interfacename` and `macaddress` fields?
        dct = {
            "interface-name": self.interface_name,
            "addresses": [dataclasses.asdict(addr) for addr in self.addresses],
        }
        if self.mac_address:
            dct["mac-address"] = self.mac_address
        return dct


@dataclasses.dataclass(frozen=True)
class Network:
    bind_addresses: List[BindAddress]
    ingress_addresses: List[str]
    egress_subnets: List[str]

    def hook_tool_output_fmt(self):
        # dumps itself to dict in the same format the hook tool would
        return {
            "bind-addresses": [ba.hook_tool_output_fmt() for ba in self.bind_addresses],
            "egress-subnets": self.egress_subnets,
            "ingress-addresses": self.ingress_addresses,
        }

    @classmethod
    def default(
        cls,
        private_address: str = "192.0.2.0",
        hostname: str = "",
        cidr: str = "",
        interface_name: str = "",
        mac_address: Optional[str] = None,
        egress_subnets=("192.0.2.0/24",),
        ingress_addresses=("192.0.2.0",),
    ) -> "Network":
        """Helper to create a minimal, heavily defaulted Network."""
        return cls(
            bind_addresses=[
                BindAddress(
                    interface_name=interface_name,
                    mac_address=mac_address,
                    addresses=[
                        Address(hostname=hostname, value=private_address, cidr=cidr),
                    ],
                ),
            ],
            egress_subnets=list(egress_subnets),
            ingress_addresses=list(ingress_addresses),
        )


_next_relation_id_counter = 1


def next_relation_id(update=True):
    global _next_relation_id_counter
    cur = _next_relation_id_counter
    if update:
        _next_relation_id_counter += 1
    return cur


@dataclasses.dataclass(frozen=True)
class _RelationBase:
    endpoint: str
    """Relation endpoint name. Must match some endpoint name defined in metadata.yaml."""

    interface: Optional[str] = None
    """Interface name. Must match the interface name attached to this endpoint in metadata.yaml.
    If left empty, it will be automatically derived from metadata.yaml."""

    id: int = dataclasses.field(default_factory=next_relation_id)
    """Juju relation ID. Every new Relation instance gets a unique one,
    if there's trouble, override."""

    local_app_data: "RawDataBagContents" = dataclasses.field(default_factory=dict)
    """This application's databag for this relation."""

    local_unit_data: "RawDataBagContents" = dataclasses.field(
        default_factory=lambda: DEFAULT_JUJU_DATABAG.copy(),
    )
    """This unit's databag for this relation."""

    @property
    def _databags(self):
        """Yield all databags in this relation."""
        yield self.local_app_data
        yield self.local_unit_data

    @property
    def _remote_unit_ids(self) -> Tuple["UnitID", ...]:
        """Ids of the units on the other end of this relation."""
        raise NotImplementedError()

    def _get_databag_for_remote(
        self,
        unit_id: int,  # noqa: U100
    ) -> "RawDataBagContents":
        """Return the databag for some remote unit ID."""
        raise NotImplementedError()

    def __post_init__(self):
        if type(self) is _RelationBase:
            raise RuntimeError(
                "_RelationBase cannot be instantiated directly; "
                "please use Relation, PeerRelation, or SubordinateRelation",
            )

        for databag in self._databags:
            self._validate_databag(databag)

    def _validate_databag(self, databag: dict):
        if not isinstance(databag, dict):
            raise StateValidationError(
                f"all databags should be dicts, not {type(databag)}",
            )
        for v in databag.values():
            if not isinstance(v, str):
                raise StateValidationError(
                    f"all databags should be Dict[str,str]; "
                    f"found a value of type {type(v)}",
                )


_DEFAULT_IP = " 192.0.2.0"
DEFAULT_JUJU_DATABAG = {
    "egress-subnets": _DEFAULT_IP,
    "ingress-address": _DEFAULT_IP,
    "private-address": _DEFAULT_IP,
}


@dataclasses.dataclass(frozen=True)
class Relation(_RelationBase):
    remote_app_name: str = "remote"

    # local limit
    limit: int = 1

    remote_app_data: "RawDataBagContents" = dataclasses.field(default_factory=dict)
    remote_units_data: Dict["UnitID", "RawDataBagContents"] = dataclasses.field(
        default_factory=lambda: {0: DEFAULT_JUJU_DATABAG.copy()},  # dedup
    )

    @property
    def _remote_app_name(self) -> str:
        """Who is on the other end of this relation?"""
        return self.remote_app_name

    @property
    def _remote_unit_ids(self) -> Tuple["UnitID", ...]:
        """Ids of the units on the other end of this relation."""
        return tuple(self.remote_units_data)

    def _get_databag_for_remote(self, unit_id: "UnitID") -> "RawDataBagContents":
        """Return the databag for some remote unit ID."""
        return self.remote_units_data[unit_id]

    @property
    def _databags(self):
        """Yield all databags in this relation."""
        yield self.local_app_data
        yield self.local_unit_data
        yield self.remote_app_data
        yield from self.remote_units_data.values()


@dataclasses.dataclass(frozen=True)
class SubordinateRelation(_RelationBase):
    remote_app_data: "RawDataBagContents" = dataclasses.field(default_factory=dict)
    remote_unit_data: "RawDataBagContents" = dataclasses.field(
        default_factory=lambda: DEFAULT_JUJU_DATABAG.copy(),
    )

    # app name and ID of the remote unit that *this unit* is attached to.
    remote_app_name: str = "remote"
    remote_unit_id: int = 0

    @property
    def _remote_unit_ids(self) -> Tuple[int]:
        """Ids of the units on the other end of this relation."""
        return (self.remote_unit_id,)

    def _get_databag_for_remote(self, unit_id: int) -> "RawDataBagContents":
        """Return the databag for some remote unit ID."""
        if unit_id is not self.remote_unit_id:
            raise ValueError(
                f"invalid unit id ({unit_id}): subordinate relation only has one "
                f"remote and that has id {self.remote_unit_id}",
            )
        return self.remote_unit_data

    @property
    def _databags(self):
        """Yield all databags in this relation."""
        yield self.local_app_data
        yield self.local_unit_data
        yield self.remote_app_data
        yield self.remote_unit_data

    @property
    def remote_unit_name(self) -> str:
        return f"{self.remote_app_name}/{self.remote_unit_id}"


@dataclasses.dataclass(frozen=True)
class PeerRelation(_RelationBase):
    peers_data: Dict["UnitID", "RawDataBagContents"] = dataclasses.field(
        default_factory=lambda: {0: DEFAULT_JUJU_DATABAG.copy()},
    )
    # mapping from peer unit IDs to their databag contents.
    # Consistency checks will validate that *this unit*'s ID is not in here.

    @property
    def _databags(self):
        """Yield all databags in this relation."""
        yield self.local_app_data
        yield self.local_unit_data
        yield from self.peers_data.values()

    @property
    def _remote_unit_ids(self) -> Tuple["UnitID", ...]:
        """Ids of the units on the other end of this relation."""
        return tuple(self.peers_data)

    def _get_databag_for_remote(self, unit_id: "UnitID") -> "RawDataBagContents":
        """Return the databag for some remote unit ID."""
        return self.peers_data[unit_id]


def _random_model_name():
    import random
    import string

    space = string.ascii_letters + string.digits
    return "".join(random.choice(space) for _ in range(20))


@dataclasses.dataclass(frozen=True)
class Model:
    name: str = dataclasses.field(default_factory=_random_model_name)
    uuid: str = dataclasses.field(default_factory=lambda: str(uuid4()))

    # whatever juju models --format=json | jq '.models[<current-model-index>].type' gives back.
    # TODO: make this exhaustive.
    type: Literal["kubernetes", "lxd"] = "kubernetes"


# for now, proc mock allows you to map one command to one mocked output.
# todo extend: one input -> multiple outputs, at different times


_CHANGE_IDS = 0


def _generate_new_change_id():
    global _CHANGE_IDS
    _CHANGE_IDS += 1
    logger.info(
        f"change ID unset; automatically assigning {_CHANGE_IDS}. "
        f"If there are problems, pass one manually.",
    )
    return _CHANGE_IDS


@dataclasses.dataclass(frozen=True)
class ExecOutput:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""

    # change ID: used internally to keep track of mocked processes
    _change_id: int = dataclasses.field(default_factory=_generate_new_change_id)

    def _run(self) -> int:
        return self._change_id


_ExecMock = Dict[Tuple[str, ...], ExecOutput]


@dataclasses.dataclass(frozen=True)
class Mount:
    location: Union[str, PurePosixPath]
    src: Union[str, Path]


@dataclasses.dataclass(frozen=True)
class Container:
    name: str
    can_connect: bool = False

    # This is the base plan. On top of it, one can add layers.
    # We need to model pebble in this way because it's impossible to retrieve the layers from
    # pebble or derive them from the resulting plan (which one CAN get from pebble).
    # So if we are instantiating Container by fetching info from a 'live' charm, the 'layers'
    # will be unknown. all that we can know is the resulting plan (the 'computed plan').
    _base_plan: dict = dataclasses.field(default_factory=dict)
    # We expect most of the user-facing testing to be covered by this 'layers' attribute,
    # as all will be known when unit-testing.
    layers: Dict[str, pebble.Layer] = dataclasses.field(default_factory=dict)

    service_status: Dict[str, pebble.ServiceStatus] = dataclasses.field(
        default_factory=dict,
    )

    # this is how you specify the contents of the filesystem: suppose you want to express that your
    # container has:
    # - /home/foo/bar.py
    # - /bin/bash
    # - /bin/baz
    #
    # this becomes:
    # mounts = {
    #     'foo': Mount('/home/foo/', Path('/path/to/local/dir/containing/bar/py/'))
    #     'bin': Mount('/bin/', Path('/path/to/local/dir/containing/bash/and/baz/'))
    # }
    # when the charm runs `pebble.pull`, it will return .open() from one of those paths.
    # when the charm pushes, it will either overwrite one of those paths (careful!) or it will
    # create a tempfile and insert its path in the mock filesystem tree
    mounts: Dict[str, Mount] = dataclasses.field(default_factory=dict)

    exec_mock: _ExecMock = dataclasses.field(default_factory=dict)

    def _render_services(self):
        # copied over from ops.testing._TestingPebbleClient._render_services()
        services = {}  # type: Dict[str, pebble.Service]
        for key in sorted(self.layers.keys()):
            layer = self.layers[key]
            for name, service in layer.services.items():
                services[name] = service
        return services

    @property
    def plan(self) -> pebble.Plan:
        """The 'computed' pebble plan.

        i.e. the base plan plus the layers that have been added on top.
        You should run your assertions on this plan, not so much on the layers, as those are
        input data.
        """

        # copied over from ops.testing._TestingPebbleClient.get_plan().
        plan = pebble.Plan(yaml.safe_dump(self._base_plan))
        services = self._render_services()
        if not services:
            return plan
        for name in sorted(services.keys()):
            plan.services[name] = services[name]
        return plan

    @property
    def services(self) -> Dict[str, pebble.ServiceInfo]:
        """The pebble services as rendered in the plan."""
        services = self._render_services()
        infos = {}  # type: Dict[str, pebble.ServiceInfo]
        names = sorted(services.keys())
        for name in names:
            try:
                service = services[name]
            except KeyError:
                # in pebble, it just returns "nothing matched" if there are 0 matches,
                # but it ignores services it doesn't recognize
                continue
            status = self.service_status.get(name, pebble.ServiceStatus.INACTIVE)
            if service.startup == "":
                startup = pebble.ServiceStartup.DISABLED
            else:
                startup = pebble.ServiceStartup(service.startup)
            info = pebble.ServiceInfo(
                name,
                startup=startup,
                current=pebble.ServiceStatus(status),
            )
            infos[name] = info
        return infos

    def get_filesystem(self, ctx: "Context") -> Path:
        """Simulated pebble filesystem in this context."""
        return ctx._get_container_root(self.name)


_RawStatusLiteral = Literal[
    "waiting",
    "blocked",
    "active",
    "unknown",
    "error",
    "maintenance",
]


@dataclasses.dataclass(frozen=True)
class _EntityStatus:
    """This class represents StatusBase and should not be interacted with directly."""

    # Why not use StatusBase directly? Because that's not json-serializable.

    name: _RawStatusLiteral
    message: str = ""

    def __eq__(self, other):
        if isinstance(other, (StatusBase, _EntityStatus)):
            return (self.name, self.message) == (other.name, other.message)
        logger.warning(
            f"Comparing Status with {other} is not stable and will be forbidden soon."
            f"Please compare with StatusBase directly.",
        )
        return super().__eq__(other)

    def __repr__(self):
        status_type_name = self.name.title() + "Status"
        if self.name == "unknown":
            return f"{status_type_name}()"
        return f"{status_type_name}('{self.message}')"


def _status_to_entitystatus(obj: StatusBase) -> _EntityStatus:
    """Convert StatusBase to _EntityStatus."""
    statusbase_subclass = type(StatusBase.from_name(obj.name, obj.message))

    class _MyClass(_EntityStatus, statusbase_subclass):
        # Custom type inheriting from a specific StatusBase subclass to support instance checks:
        #  isinstance(state.unit_status, ops.ActiveStatus)
        pass

    return _MyClass(cast(_RawStatusLiteral, obj.name), obj.message)


@dataclasses.dataclass(frozen=True)
class StoredState:
    # /-separated Object names. E.g. MyCharm/MyCharmLib.
    # if None, this StoredState instance is owned by the Framework.
    owner_path: Optional[str]

    name: str = "_stored"
    # Ideally, the type here would be only marshallable types, rather than Any.
    # However, it's complex to describe those types, since it's a recursive
    # definition - even in TypeShed the _Marshallable type includes containers
    # like list[Any], which seems to defeat the point.
    content: Dict[str, Any] = dataclasses.field(default_factory=dict)

    _data_type_name: str = "StoredStateData"

    @property
    def handle_path(self):
        return f"{self.owner_path or ''}/{self._data_type_name}[{self.name}]"


_RawPortProtocolLiteral = Literal["tcp", "udp", "icmp"]


@dataclasses.dataclass(frozen=True)
class Port:
    """Represents a port on the charm host."""

    protocol: _RawPortProtocolLiteral
    port: Optional[int] = None
    """The port to open. Required for TCP and UDP; not allowed for ICMP."""

    def __post_init__(self):
        port = self.port
        is_icmp = self.protocol == "icmp"
        if port:
            if is_icmp:
                raise StateValidationError(
                    "`port` arg not supported with `icmp` protocol",
                )
            if not (1 <= port <= 65535):
                raise StateValidationError(
                    f"`port` outside bounds [1:65535], got {port}",
                )
        elif not is_icmp:
            raise StateValidationError(
                f"`port` arg required with `{self.protocol}` protocol",
            )


_next_storage_index_counter = 0  # storage indices start at 0


def next_storage_index(update=True):
    """Get the index (used to be called ID) the next Storage to be created will get.

    Pass update=False if you're only inspecting it.
    Pass update=True if you also want to bump it.
    """
    global _next_storage_index_counter
    cur = _next_storage_index_counter
    if update:
        _next_storage_index_counter += 1
    return cur


@dataclasses.dataclass(frozen=True)
class Storage:
    """Represents an (attached!) storage made available to the charm container."""

    name: str

    index: int = dataclasses.field(default_factory=next_storage_index)
    # Every new Storage instance gets a new one, if there's trouble, override.

    def get_filesystem(self, ctx: "Context") -> Path:
        """Simulated filesystem root in this context."""
        return ctx._get_storage_root(self.name, self.index)


@dataclasses.dataclass(frozen=True)
class State:
    """Represents the juju-owned portion of a unit's state.

    Roughly speaking, it wraps all hook-tool- and pebble-mediated data a charm can access in its
    lifecycle. For example, status-get will return data from `State.status`, is-leader will
    return data from `State.leader`, and so on.
    """

    config: Dict[str, Union[str, int, float, bool]] = dataclasses.field(
        default_factory=dict,
    )
    """The present configuration of this charm."""
    relations: List["AnyRelation"] = dataclasses.field(default_factory=list)
    """All relations that currently exist for this charm."""
    networks: Dict[str, Network] = dataclasses.field(default_factory=dict)
    """Manual overrides for any relation and extra bindings currently provisioned for this charm.
    If a metadata-defined relation endpoint is not explicitly mapped to a Network in this field,
    it will be defaulted.
    [CAVEAT: `extra-bindings` is a deprecated, regretful feature in juju/ops. For completeness we
    support it, but use at your own risk.] If a metadata-defined extra-binding is left empty,
    it will be defaulted.
    """
    containers: List[Container] = dataclasses.field(default_factory=list)
    """All containers (whether they can connect or not) that this charm is aware of."""
    storage: List[Storage] = dataclasses.field(default_factory=list)
    """All ATTACHED storage instances for this charm.
    If a storage is not attached, omit it from this listing."""

    # we don't use sets to make json serialization easier
    opened_ports: List[Port] = dataclasses.field(default_factory=list)
    """Ports opened by juju on this charm."""
    leader: bool = False
    """Whether this charm has leadership."""
    model: Model = Model()
    """The model this charm lives in."""
    secrets: List[Secret] = dataclasses.field(default_factory=list)
    """The secrets this charm has access to (as an owner, or as a grantee).
    The presence of a secret in this list entails that the charm can read it.
    Whether it can manage it or not depends on the individual secret's `owner` flag."""
    resources: Dict[str, "PathLike"] = dataclasses.field(default_factory=dict)
    """Mapping from resource name to path at which the resource can be found."""
    planned_units: int = 1
    """Number of non-dying planned units that are expected to be running this application.
    Use with caution."""

    # represents the OF's event queue. These events will be emitted before the event being
    # dispatched, and represent the events that had been deferred during the previous run.
    # If the charm defers any events during "this execution", they will be appended
    # to this list.
    deferred: List["DeferredEvent"] = dataclasses.field(default_factory=list)
    """Events that have been deferred on this charm by some previous execution."""
    stored_state: List["StoredState"] = dataclasses.field(default_factory=list)
    """Contents of a charm's stored state."""

    # the current statuses. Will be cast to _EntitiyStatus in __post_init__
    app_status: Union[StatusBase, _EntityStatus] = _EntityStatus("unknown")
    """Status of the application."""
    unit_status: Union[StatusBase, _EntityStatus] = _EntityStatus("unknown")
    """Status of the unit."""
    workload_version: str = ""
    """Workload version."""

    def __post_init__(self):
        for name in ["app_status", "unit_status"]:
            val = getattr(self, name)
            if isinstance(val, _EntityStatus):
                pass
            elif isinstance(val, StatusBase):
                object.__setattr__(self, name, _status_to_entitystatus(val))
            else:
                raise TypeError(f"Invalid status.{name}: {val!r}")

    def _update_workload_version(self, new_workload_version: str):
        """Update the current app version and record the previous one."""
        # We don't keep a full history because we don't expect the app version to change more
        # than once per hook.

        # bypass frozen dataclass
        object.__setattr__(self, "workload_version", new_workload_version)

    def _update_status(
        self,
        new_status: _RawStatusLiteral,
        new_message: str = "",
        is_app: bool = False,
    ):
        """Update the current app/unit status and add the previous one to the history."""
        name = "app_status" if is_app else "unit_status"
        # bypass frozen dataclass
        object.__setattr__(self, name, _EntityStatus(new_status, new_message))

    def with_can_connect(self, container_name: str, can_connect: bool) -> "State":
        def replacer(container: Container):
            if container.name == container_name:
                return dataclasses.replace(container, can_connect=can_connect)
            return container

        ctrs = tuple(map(replacer, self.containers))
        return dataclasses.replace(self, containers=ctrs)

    def with_leadership(self, leader: bool) -> "State":
        return dataclasses.replace(self, leader=leader)

    def with_unit_status(self, status: StatusBase) -> "State":
        return dataclasses.replace(
            self,
            status=dataclasses.replace(
                cast(_EntityStatus, self.unit_status),
                unit=_status_to_entitystatus(status),
            ),
        )

    def get_container(self, container: Union[str, Container]) -> Container:
        """Get container from this State, based on an input container or its name."""
        container_name = (
            container.name if isinstance(container, Container) else container
        )
        containers = [c for c in self.containers if c.name == container_name]
        if not containers:
            raise ValueError(f"container: {container_name} not found in the State")
        return containers[0]

    def get_relations(self, endpoint: str) -> Tuple["AnyRelation", ...]:
        """Get all relations on this endpoint from the current state."""

        # we rather normalize the endpoint than worry about cursed metadata situations such as:
        # requires:
        #   foo-bar: ...
        #   foo_bar: ...

        normalized_endpoint = normalize_name(endpoint)
        return tuple(
            r
            for r in self.relations
            if normalize_name(r.endpoint) == normalized_endpoint
        )

    def get_storages(self, name: str) -> Tuple["Storage", ...]:
        """Get all storages with this name."""
        return tuple(s for s in self.storage if s.name == name)


def _is_valid_charmcraft_25_metadata(meta: Dict[str, Any]):
    # Check whether this dict has the expected mandatory metadata fields according to the
    # charmcraft >2.5 charmcraft.yaml schema
    if (config_type := meta.get("type")) != "charm":
        logger.debug(
            f"Not a charm: charmcraft yaml config ``.type`` is {config_type!r}.",
        )
        return False
    if not all(field in meta for field in {"name", "summary", "description"}):
        logger.debug("Not a charm: charmcraft yaml misses some required fields")
        return False
    return True


@dataclasses.dataclass(frozen=True)
class _CharmSpec(Generic[CharmType]):
    """Charm spec."""

    charm_type: Type[CharmBase]
    meta: Dict[str, Any]
    actions: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None

    # autoloaded means: we are running a 'real' charm class, living in some
    # /src/charm.py, and the metadata files are 'real' metadata files.
    is_autoloaded: bool = False

    @staticmethod
    def _load_metadata_legacy(charm_root: Path):
        """Load metadata from charm projects created with Charmcraft < 2.5."""
        # back in the days, we used to have separate metadata.yaml, config.yaml and actions.yaml
        # files for charm metadata.
        metadata_path = charm_root / "metadata.yaml"
        meta = yaml.safe_load(metadata_path.open()) if metadata_path.exists() else {}

        config_path = charm_root / "config.yaml"
        config = yaml.safe_load(config_path.open()) if config_path.exists() else None

        actions_path = charm_root / "actions.yaml"
        actions = yaml.safe_load(actions_path.open()) if actions_path.exists() else None
        return meta, config, actions

    @staticmethod
    def _load_metadata(charm_root: Path):
        """Load metadata from charm projects created with Charmcraft >= 2.5."""
        metadata_path = charm_root / "charmcraft.yaml"
        meta = yaml.safe_load(metadata_path.open()) if metadata_path.exists() else {}
        if not _is_valid_charmcraft_25_metadata(meta):
            meta = {}
        config = meta.pop("config", None)
        actions = meta.pop("actions", None)
        return meta, config, actions

    @staticmethod
    def autoload(charm_type: Type[CharmBase]) -> "_CharmSpec[CharmType]":
        """Construct a ``_CharmSpec`` object by looking up the metadata from the charm's repo root.

        Will attempt to load the metadata off the ``charmcraft.yaml`` file
        """
        charm_source_path = Path(inspect.getfile(charm_type))
        charm_root = charm_source_path.parent.parent

        # attempt to load metadata from unified charmcraft.yaml
        meta, config, actions = _CharmSpec._load_metadata(charm_root)

        if not meta:
            # try to load using legacy metadata.yaml/actions.yaml/config.yaml files
            meta, config, actions = _CharmSpec._load_metadata_legacy(charm_root)

        if not meta:
            # still no metadata? bug out
            raise MetadataNotFoundError(
                f"invalid charm root {charm_root!r}; "
                f"expected to contain at least a `charmcraft.yaml` file "
                f"(or a `metadata.yaml` file if it's an old charm).",
            )

        return _CharmSpec(
            charm_type=charm_type,
            meta=meta,
            actions=actions,
            config=config,
            is_autoloaded=True,
        )

    def get_all_relations(self) -> List[Tuple[str, Dict[str, str]]]:
        """A list of all relation endpoints defined in the metadata."""
        return list(
            chain(
                self.meta.get("requires", {}).items(),
                self.meta.get("provides", {}).items(),
                self.meta.get("peers", {}).items(),
            ),
        )


@dataclasses.dataclass(frozen=True)
class DeferredEvent:
    handle_path: str
    owner: str
    observer: str

    # needs to be marshal.dumps-able.
    snapshot_data: Dict = dataclasses.field(default_factory=dict)

    @property
    def name(self):
        return self.handle_path.split("/")[-1].split("[")[0]


class _EventType(str, Enum):
    framework = "framework"
    builtin = "builtin"
    relation = "relation"
    action = "action"
    secret = "secret"
    storage = "storage"
    workload = "workload"
    custom = "custom"


class _EventPath(str):
    if TYPE_CHECKING:  # pragma: no cover
        name: str
        owner_path: List[str]
        suffix: str
        prefix: str
        is_custom: bool
        type: _EventType

    def __new__(cls, string):
        string = normalize_name(string)
        instance = super().__new__(cls, string)

        instance.name = name = string.split(".")[-1]
        instance.owner_path = string.split(".")[:-1] or ["on"]

        instance.suffix, instance.type = suffix, _ = _EventPath._get_suffix_and_type(
            name,
        )
        if suffix:
            instance.prefix, _ = string.rsplit(suffix)
        else:
            instance.prefix = string

        instance.is_custom = suffix == ""
        return instance

    @staticmethod
    def _get_suffix_and_type(s: str) -> Tuple[str, _EventType]:
        for suffix in RELATION_EVENTS_SUFFIX:
            if s.endswith(suffix):
                return suffix, _EventType.relation

        if s.endswith(ACTION_EVENT_SUFFIX):
            return ACTION_EVENT_SUFFIX, _EventType.action

        if s in SECRET_EVENTS:
            return s, _EventType.secret

        if s in FRAMEWORK_EVENTS:
            return s, _EventType.framework

        # Whether the event name indicates that this is a storage event.
        for suffix in STORAGE_EVENTS_SUFFIX:
            if s.endswith(suffix):
                return suffix, _EventType.storage

        # Whether the event name indicates that this is a workload event.
        if s.endswith(PEBBLE_READY_EVENT_SUFFIX):
            return PEBBLE_READY_EVENT_SUFFIX, _EventType.workload

        if s in BUILTIN_EVENTS:
            return "", _EventType.builtin

        return "", _EventType.custom


@dataclasses.dataclass(frozen=True)
class _Event:
    path: str
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = dataclasses.field(default_factory=dict)

    # if this is a storage event, the storage it refers to
    storage: Optional["Storage"] = None
    # if this is a relation event, the relation it refers to
    relation: Optional["AnyRelation"] = None
    # and the name of the remote unit this relation event is about
    relation_remote_unit_id: Optional[int] = None
    # and the name of the unit that is departing if this is -relation-departed.
    relation_departed_unit_id: Optional[int] = None

    # if this is a secret event, the secret it refers to
    secret: Optional[Secret] = None
    # if this is a secret-removed or secret-expired event, the secret revision it refers to
    secret_revision: Optional[int] = None

    # if this is a workload (container) event, the container it refers to
    container: Optional[Container] = None

    # if this is an action event, the Action instance
    action: Optional["Action"] = None

    _owner_path: List[str] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        path = _EventPath(self.path)
        # bypass frozen dataclass
        object.__setattr__(self, "path", path)

    @property
    def _path(self) -> _EventPath:
        # we converted it in __post_init__, but the type checker doesn't know about that
        return cast(_EventPath, self.path)

    @property
    def name(self) -> str:
        """Full event name.

        Consists of a 'prefix' and a 'suffix'. The suffix denotes the type of the event, the
        prefix the name of the entity the event is about.

        "foo-relation-changed":
         - "foo"=prefix (name of a relation),
         - "-relation-changed"=suffix (relation event)
        """
        return self._path.name

    @property
    def owner_path(self) -> List[str]:
        """Path to the ObjectEvents instance owning this event.

        If this event is defined on the toplevel charm class, it should be ['on'].
        """
        return self._path.owner_path

    @property
    def _is_relation_event(self) -> bool:
        """Whether the event name indicates that this is a relation event."""
        return self._path.type is _EventType.relation

    @property
    def _is_action_event(self) -> bool:
        """Whether the event name indicates that this is a relation event."""
        return self._path.type is _EventType.action

    @property
    def _is_secret_event(self) -> bool:
        """Whether the event name indicates that this is a secret event."""
        return self._path.type is _EventType.secret

    @property
    def _is_storage_event(self) -> bool:
        """Whether the event name indicates that this is a storage event."""
        return self._path.type is _EventType.storage

    @property
    def _is_workload_event(self) -> bool:
        """Whether the event name indicates that this is a workload event."""
        return self._path.type is _EventType.workload

    # this method is private because _CharmSpec is not quite user-facing; also,
    # the user should know.
    def _is_builtin_event(self, charm_spec: "_CharmSpec"):
        """Determine whether the event is a custom-defined one or a builtin one."""
        event_name = self.name

        # simple case: this is an event type owned by our charm base.on
        if hasattr(charm_spec.charm_type.on, event_name):
            return hasattr(CharmEvents, event_name)

        # this could be an event defined on some other Object, e.g. a charm lib.
        # We don't support (yet) directly emitting those, but they COULD have names that conflict
        # with events owned by the base charm. E.g. if the charm has a `foo` relation, the charm
        # will get a  charm.on.foo_relation_created. Your charm lib is free to define its own
        # `foo_relation_created`  custom event, because its handle will be
        # `charm.lib.on.foo_relation_created` and therefore be  unique and the Framework is happy.
        # However, our Event data structure ATM has no knowledge of which Object/Handle it is
        # owned by. So the only thing we can do right now is: check whether the event name,
        # assuming it is owned by the charm, LOOKS LIKE that of a builtin event or not.
        return self._path.type is not _EventType.custom

    def deferred(self, handler: Callable, event_id: int = 1) -> DeferredEvent:
        """Construct a DeferredEvent from this Event."""
        handler_repr = repr(handler)
        handler_re = re.compile(r"<function (.*) at .*>")
        match = handler_re.match(handler_repr)
        if not match:
            raise ValueError(
                f"cannot construct DeferredEvent from {handler}; please create one manually.",
            )
        owner_name, handler_name = match.groups()[0].split(".")[-2:]
        handle_path = f"{owner_name}/on/{self.name}[{event_id}]"

        snapshot_data = {}

        # fixme: at this stage we can't determine if the event is a builtin one or not; if it is
        #  not, then the coming checks are meaningless: the custom event could be named like a
        #  relation event but not *be* one.
        if self._is_workload_event:
            # this is a WorkloadEvent. The snapshot:
            container = cast(Container, self.container)
            snapshot_data = {
                "container_name": container.name,
            }

        elif self._is_relation_event:
            # this is a RelationEvent.
            relation = cast("AnyRelation", self.relation)
            if isinstance(relation, PeerRelation):
                # FIXME: relation.unit for peers should point to <this unit>, but we
                #  don't have access to the local app name in this context.
                remote_app = "local"
            else:
                remote_app = relation.remote_app_name

            snapshot_data = {
                "relation_name": relation.endpoint,
                "relation_id": relation.id,
                "app_name": remote_app,
                "unit_name": f"{remote_app}/{self.relation_remote_unit_id}",
            }

        return DeferredEvent(
            handle_path,
            owner_name,
            handler_name,
            snapshot_data=snapshot_data,
        )


_next_action_id_counter = 1


def next_action_id(update=True):
    global _next_action_id_counter
    cur = _next_action_id_counter
    if update:
        _next_action_id_counter += 1
    # Juju currently uses numbers for the ID, but in the past used UUIDs, so
    # we need these to be strings.
    return str(cur)


@dataclasses.dataclass(frozen=True)
class Action:
    name: str

    params: Dict[str, "AnyJson"] = dataclasses.field(default_factory=dict)

    id: str = dataclasses.field(default_factory=next_action_id)
    """Juju action ID.

    Every action invocation is automatically assigned a new one. Override in
    the rare cases where a specific ID is required."""

    @property
    def event(self) -> _Event:
        """Helper to generate an action event from this action."""
        return _Event(self.name + ACTION_EVENT_SUFFIX, action=self)


def deferred(
    event: Union[str, _Event],
    handler: Callable,
    event_id: int = 1,
    relation: Optional["Relation"] = None,
    container: Optional["Container"] = None,
):
    """Construct a DeferredEvent from an Event or an event name."""
    if isinstance(event, str):
        event = _Event(event, relation=relation, container=container)
    return event.deferred(handler=handler, event_id=event_id)
