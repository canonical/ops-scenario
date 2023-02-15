from typing import Optional, Union

import pytest
from ops.charm import CharmBase
from ops.model import _ModelBackend

from scenario.mocking import _MockModelBackend
from scenario.state import State, Event, _CharmSpec, Relation


# todo expose as context vars or through pytest-operator
MODEL_NAME = "secret-demo"
UNIT_NAME = "holder/0"


class RemoteUnitBackend(_ModelBackend):
    def __init__(self, state: "State", event: "Event", charm_spec: "_CharmSpec"):
        super().__init__(state.unit_name, state.model.name, state.model.uuid)

    def _run(self, *args: str, return_output: bool = False, use_json: bool = False,
             input_stream: Optional[str] = None) -> Union[str, 'JsonObject', None]:
        args = tuple(f"juju exec -m {MODEL_NAME} -u {UNIT_NAME} --".split()) + args
        return super()._run(*args, return_output=return_output, use_json=use_json, input_stream=input_stream)


def get_res(obj, method_name, args, kwargs):
    method = getattr(obj, method_name)
    try:
        result = method(*args, **kwargs)
    except Exception as e:
        return 1, e
    return 0, result


def compare(state, event, charm_spec,
            method_name, args, kwargs,
            fail=False):
    mock_backend = _MockModelBackend(
        state=state, event=event, charm_spec=charm_spec
    )
    mock_retcode, mock_result = get_res(mock_backend, method_name, args, kwargs)

    remote_backend = RemoteUnitBackend(
        state=state, event=event, charm_spec=charm_spec
    )
    remote_retcode, remote_result = get_res(remote_backend, method_name, args, kwargs)

    error = False
    if fail:
        if not mock_retcode == remote_retcode == 1:
            error = 'different return codes'

        # compare the exceptions
        if not type(mock_result) == type(remote_result):
            error = 'different error types'
        # if not mock_result.args == remote_result.args:
        #     error = 'different error args'

    else:
        if not mock_retcode == remote_retcode == 0:
            error = 'different return codes'
        if not mock_result == remote_result:
            error = f'results are different: mock:{mock_result} != remote:{remote_result}'

    if error:
        raise RuntimeError(error)

class MyCharm(CharmBase):
    META = {
        'name': 'holder',
        'requires': {'secret_id': {"interface": 'secret-id-demo'}}
    }


def check_call(
        method_name,
        *args,
        fail=False,
        **kwargs
):
    compare(
        State(relations=[
            Relation('secret_id',
                     interface='secret-id-demo',
                     remote_app_name='owner',
                     relation_id=1)
        ]),
        Event('start'),
        _CharmSpec(MyCharm, meta=MyCharm.META),
        method_name,
        args,
        kwargs,
        fail=fail
    )


def check_pass(
        method_name,
        *args,
        **kwargs
):
    return check_call(
        method_name,
        *args,
        **kwargs
    )


def check_fail(
        method_name,
        *args,
        **kwargs
):
    return check_call(
        method_name,
        *args,
        fail=True,
        **kwargs
    )
