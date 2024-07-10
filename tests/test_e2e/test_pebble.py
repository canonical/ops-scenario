import dataclasses
import datetime
import tempfile
from pathlib import Path

import pytest
from ops import PebbleCustomNoticeEvent, pebble
from ops.charm import CharmBase
from ops.framework import Framework
from ops.pebble import ExecError, ServiceStartup, ServiceStatus

from scenario import Context
from scenario.state import Check, Container, ExecOutput, Mount, Notice, State
from tests.helpers import jsonpatch_delta, trigger


@pytest.fixture(scope="function")
def charm_cls():
    class MyCharm(CharmBase):
        def __init__(self, framework: Framework):
            super().__init__(framework)
            for evt in self.on.events().values():
                self.framework.observe(evt, self._on_event)

        def _on_event(self, event):
            pass

    return MyCharm


def test_no_containers(charm_cls):
    def callback(self: CharmBase):
        assert not self.unit.containers

    trigger(
        State(),
        charm_type=charm_cls,
        meta={"name": "foo"},
        event="start",
        post_event=callback,
    )


def test_containers_from_meta(charm_cls):
    def callback(self: CharmBase):
        assert self.unit.containers
        assert self.unit.get_container("foo")

    trigger(
        State(),
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="start",
        post_event=callback,
    )


@pytest.mark.parametrize("can_connect", (True, False))
def test_connectivity(charm_cls, can_connect):
    def callback(self: CharmBase):
        assert can_connect == self.unit.get_container("foo").can_connect()

    trigger(
        State(containers={Container(name="foo", can_connect=can_connect)}),
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="start",
        post_event=callback,
    )


def test_fs_push(charm_cls):
    text = "lorem ipsum/n alles amat gloriae foo"
    file = tempfile.NamedTemporaryFile()
    pth = Path(file.name)
    pth.write_text(text)

    def callback(self: CharmBase):
        container = self.unit.get_container("foo")
        baz = container.pull("/bar/baz.txt")
        assert baz.read() == text

    trigger(
        State(
            containers={
                Container(
                    name="foo",
                    can_connect=True,
                    mounts={"bar": Mount(location="/bar/baz.txt", source=pth)},
                )
            }
        ),
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="start",
        post_event=callback,
    )


@pytest.mark.parametrize("make_dirs", (True, False))
def test_fs_pull(charm_cls, make_dirs):
    text = "lorem ipsum/n alles amat gloriae foo"

    def callback(self: CharmBase):
        container = self.unit.get_container("foo")
        if make_dirs:
            container.push("/foo/bar/baz.txt", text, make_dirs=make_dirs)
            # check that pulling immediately 'works'
            baz = container.pull("/foo/bar/baz.txt")
            assert baz.read() == text
        else:
            with pytest.raises(pebble.PathError):
                container.push("/foo/bar/baz.txt", text, make_dirs=make_dirs)

            # check that nothing was changed
            with pytest.raises((FileNotFoundError, pebble.PathError)):
                container.pull("/foo/bar/baz.txt")

    td = tempfile.TemporaryDirectory()
    container = Container(
        name="foo",
        can_connect=True,
        mounts={"foo": Mount(location="/foo", source=td.name)},
    )
    state = State(containers={container})

    ctx = Context(
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
    )
    with ctx.manager(ctx.on.start(), state=state) as mgr:
        out = mgr.run()
        callback(mgr.charm)

    if make_dirs:
        # file = (out.get_container("foo").mounts["foo"].source + "bar/baz.txt").open("/foo/bar/baz.txt")

        # this is one way to retrieve the file
        file = Path(td.name + "/bar/baz.txt")

        # another is:
        assert (
            file
            == Path(out.get_container("foo").mounts["foo"].source) / "bar" / "baz.txt"
        )

        # but that is actually a symlink to the context's root tmp folder:
        assert (
            Path(ctx._tmp.name) / "containers" / "foo" / "foo" / "bar" / "baz.txt"
        ).read_text() == text
        assert file.read_text() == text

        # shortcut for API niceness purposes:
        file = container.get_filesystem(ctx) / "foo" / "bar" / "baz.txt"
        assert file.read_text() == text

    else:
        # nothing has changed
        out_purged = dataclasses.replace(out, stored_states=state.stored_states)
        assert not jsonpatch_delta(out_purged, state)


LS = """
.rw-rw-r--  228 ubuntu ubuntu 18 jan 12:05 -- charmcraft.yaml    
.rw-rw-r--  497 ubuntu ubuntu 18 jan 12:05 -- config.yaml        
.rw-rw-r--  900 ubuntu ubuntu 18 jan 12:05 -- CONTRIBUTING.md    
drwxrwxr-x    - ubuntu ubuntu 18 jan 12:06 -- lib                
.rw-rw-r--  11k ubuntu ubuntu 18 jan 12:05 -- LICENSE            
.rw-rw-r-- 1,6k ubuntu ubuntu 18 jan 12:05 -- metadata.yaml      
.rw-rw-r--  845 ubuntu ubuntu 18 jan 12:05 -- pyproject.toml     
.rw-rw-r--  831 ubuntu ubuntu 18 jan 12:05 -- README.md          
.rw-rw-r--   13 ubuntu ubuntu 18 jan 12:05 -- requirements.txt   
drwxrwxr-x    - ubuntu ubuntu 18 jan 12:05 -- src                
drwxrwxr-x    - ubuntu ubuntu 18 jan 12:05 -- tests              
.rw-rw-r-- 1,9k ubuntu ubuntu 18 jan 12:05 -- tox.ini            
"""
PS = """
    PID TTY          TIME CMD    
 298238 pts/3    00:00:04 zsh    
1992454 pts/3    00:00:00 ps     
"""


@pytest.mark.parametrize(
    "cmd, out",
    (
        ("ls", LS),
        ("ps", PS),
    ),
)
def test_exec(charm_cls, cmd, out):
    def callback(self: CharmBase):
        container = self.unit.get_container("foo")
        proc = container.exec([cmd])
        proc.wait()
        assert proc.stdout.read() == "hello pebble"

    trigger(
        State(
            containers={
                Container(
                    name="foo",
                    can_connect=True,
                    exec_mock={(cmd,): ExecOutput(stdout="hello pebble")},
                )
            }
        ),
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="start",
        post_event=callback,
    )


def test_pebble_ready(charm_cls):
    def callback(self: CharmBase):
        foo = self.unit.get_container("foo")
        assert foo.can_connect()

    container = Container(name="foo", can_connect=True)

    trigger(
        State(containers={container}),
        charm_type=charm_cls,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="pebble_ready",
        post_event=callback,
    )


@pytest.mark.parametrize("starting_service_status", pebble.ServiceStatus)
def test_pebble_plan(charm_cls, starting_service_status):
    class PlanCharm(charm_cls):
        def __init__(self, framework):
            super().__init__(framework)
            framework.observe(self.on.foo_pebble_ready, self._on_ready)

        def _on_ready(self, event):
            foo = event.workload

            assert foo.get_plan().to_dict() == {
                "services": {"fooserv": {"startup": "enabled"}}
            }
            fooserv = foo.get_services("fooserv")["fooserv"]
            assert fooserv.startup == ServiceStartup.ENABLED
            assert fooserv.current == ServiceStatus.ACTIVE

            foo.add_layer(
                "bar",
                {
                    "summary": "bla",
                    "description": "deadbeef",
                    "services": {"barserv": {"startup": "disabled"}},
                },
            )

            foo.replan()
            assert foo.get_plan().to_dict() == {
                "services": {
                    "barserv": {"startup": "disabled"},
                    "fooserv": {"startup": "enabled"},
                }
            }

            assert foo.get_service("barserv").current == starting_service_status
            foo.start("barserv")
            # whatever the original state, starting a service sets it to active
            assert foo.get_service("barserv").current == ServiceStatus.ACTIVE

    container = Container(
        name="foo",
        can_connect=True,
        layers={
            "foo": pebble.Layer(
                {
                    "summary": "bla",
                    "description": "deadbeef",
                    "services": {"fooserv": {"startup": "enabled"}},
                }
            )
        },
        service_status={
            "fooserv": pebble.ServiceStatus.ACTIVE,
            # todo: should we disallow setting status for services that aren't known YET?
            "barserv": starting_service_status,
        },
    )

    out = trigger(
        State(containers={container}),
        charm_type=PlanCharm,
        meta={"name": "foo", "containers": {"foo": {}}},
        event="pebble_ready",
    )

    serv = lambda name, obj: pebble.Service(name, raw=obj)
    container = out.get_container(container.name)
    assert container.plan.services == {
        "barserv": serv("barserv", {"startup": "disabled"}),
        "fooserv": serv("fooserv", {"startup": "enabled"}),
    }
    assert container.services["fooserv"].current == pebble.ServiceStatus.ACTIVE
    assert container.services["fooserv"].startup == pebble.ServiceStartup.ENABLED

    assert container.services["barserv"].current == pebble.ServiceStatus.ACTIVE
    assert container.services["barserv"].startup == pebble.ServiceStartup.DISABLED


def test_exec_wait_error(charm_cls):
    state = State(
        containers={
            Container(
                name="foo",
                can_connect=True,
                exec_mock={("foo",): ExecOutput(stdout="hello pebble", return_code=1)},
            )
        }
    )

    ctx = Context(charm_cls, meta={"name": "foo", "containers": {"foo": {}}})
    with ctx.manager(ctx.on.start(), state) as mgr:
        container = mgr.charm.unit.get_container("foo")
        proc = container.exec(["foo"])
        with pytest.raises(ExecError):
            proc.wait()
        assert proc.stdout.read() == "hello pebble"


def test_exec_wait_output(charm_cls):
    state = State(
        containers={
            Container(
                name="foo",
                can_connect=True,
                exec_mock={
                    ("foo",): ExecOutput(stdout="hello pebble", stderr="oepsie")
                },
            )
        }
    )

    ctx = Context(charm_cls, meta={"name": "foo", "containers": {"foo": {}}})
    with ctx.manager(ctx.on.start(), state) as mgr:
        container = mgr.charm.unit.get_container("foo")
        proc = container.exec(["foo"])
        out, err = proc.wait_output()
        assert out == "hello pebble"
        assert err == "oepsie"


def test_exec_wait_output_error(charm_cls):
    state = State(
        containers={
            Container(
                name="foo",
                can_connect=True,
                exec_mock={("foo",): ExecOutput(stdout="hello pebble", return_code=1)},
            )
        }
    )

    ctx = Context(charm_cls, meta={"name": "foo", "containers": {"foo": {}}})
    with ctx.manager(ctx.on.start(), state) as mgr:
        container = mgr.charm.unit.get_container("foo")
        proc = container.exec(["foo"])
        with pytest.raises(ExecError):
            proc.wait_output()


def test_pebble_custom_notice(charm_cls):
    notices = [
        Notice(key="example.com/foo"),
        Notice(key="example.com/bar", last_data={"a": "b"}),
        Notice(key="example.com/baz", occurrences=42),
    ]
    container = Container(
        name="foo",
        can_connect=True,
        notices=notices,
    )

    state = State(containers=[container])
    ctx = Context(charm_cls, meta={"name": "foo", "containers": {"foo": {}}})
    with ctx.manager(
        ctx.on.pebble_custom_notice(container=container, notice=notices[-1]), state
    ) as mgr:
        container = mgr.charm.unit.get_container("foo")
        assert container.get_notices() == [n._to_ops() for n in notices]


def test_pebble_custom_notice_in_charm():
    key = "example.com/test/charm"
    data = {"foo": "bar"}
    user_id = 100
    first_occurred = datetime.datetime(1979, 1, 25, 11, 0, 0)
    last_occured = datetime.datetime(2006, 8, 28, 13, 28, 0)
    last_repeated = datetime.datetime(2023, 9, 4, 9, 0, 0)
    occurrences = 42
    repeat_after = datetime.timedelta(days=7)
    expire_after = datetime.timedelta(days=365)

    class MyCharm(CharmBase):
        def __init__(self, framework):
            super().__init__(framework)
            framework.observe(self.on.foo_pebble_custom_notice, self._on_custom_notice)

        def _on_custom_notice(self, event: PebbleCustomNoticeEvent):
            notice = event.notice
            assert notice.type == pebble.NoticeType.CUSTOM
            assert notice.key == key
            assert notice.last_data == data
            assert notice.user_id == user_id
            assert notice.first_occurred == first_occurred
            assert notice.last_occurred == last_occured
            assert notice.last_repeated == last_repeated
            assert notice.occurrences == occurrences
            assert notice.repeat_after == repeat_after
            assert notice.expire_after == expire_after

    notices = [
        Notice("example.com/test/other"),
        Notice("example.org/test/charm", last_data={"foo": "baz"}),
        Notice(
            key,
            last_data=data,
            user_id=user_id,
            first_occurred=first_occurred,
            last_occurred=last_occured,
            last_repeated=last_repeated,
            occurrences=occurrences,
            repeat_after=repeat_after,
            expire_after=expire_after,
        ),
    ]
    container = Container(
        name="foo",
        can_connect=True,
        notices=notices,
    )
    state = State(containers=[container])
    ctx = Context(MyCharm, meta={"name": "foo", "containers": {"foo": {}}})
    ctx.run(ctx.on.pebble_custom_notice(container=container, notice=notices[-1]), state)


def test_pebble_check_failed():
    class MyCharm(CharmBase):
        def __init__(self, framework):
            super().__init__(framework)
            framework.observe(self.on.foo_pebble_check_failed, self._on_check_failed)

        def _on_check_failed(self, event):
            info = event.info
            assert info.name == "http-check"
            assert info.status == pebble.CheckStatus.DOWN
            assert info.failures == 7

    ctx = Context(MyCharm, meta={"name": "foo", "containers": {"foo": {}}})
    check = Check("http-check", failures=7, status=pebble.CheckStatus.DOWN)
    container = Container("foo", checks={check})
    state = State(containers={container})
    ctx.run(ctx.on.pebble_check_failed(check=check, container=container), state=state)


def test_pebble_check_recovered():
    class MyCharm(CharmBase):
        def __init__(self, framework):
            super().__init__(framework)
            framework.observe(
                self.on.foo_pebble_check_recovered, self._on_check_recovered
            )

        def _on_check_recovered(self, event):
            info = event.info
            assert info.name == "http-check"
            assert info.status == pebble.CheckStatus.UP
            assert info.failures == 0

    ctx = Context(MyCharm, meta={"name": "foo", "containers": {"foo": {}}})
    check = Check("http-check")
    container = Container("foo", checks={check})
    state = State(containers={container})
    ctx.run(
        ctx.on.pebble_check_recovered(check=check, container=container), state=state
    )
