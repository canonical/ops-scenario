#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import copy
import dataclasses
import typing
from itertools import chain
from typing import Any, Callable, Dict, Iterable, Optional, TextIO, Type, Union

from scenario import Context
from scenario.logger import logger as scenario_logger
from scenario.state import (
    ATTACH_ALL_STORAGES,
    BREAK_ALL_RELATIONS,
    CREATE_ALL_RELATIONS,
    DETACH_ALL_STORAGES,
    State,
    _Event,
)

if typing.TYPE_CHECKING:  # pragma: no cover
    from ops.testing import CharmType

CharmMeta = Optional[Union[str, TextIO, dict]]
logger = scenario_logger.getChild("scenario")


def decompose_meta_event(meta_event: _Event, state: State):
    # decompose the meta event

    if meta_event.name in [ATTACH_ALL_STORAGES, DETACH_ALL_STORAGES]:
        logger.warning(f"meta-event {meta_event.name} not supported yet")
        return

    is_rel_created_meta_event = meta_event.name == CREATE_ALL_RELATIONS
    is_rel_broken_meta_event = meta_event.name == BREAK_ALL_RELATIONS
    if is_rel_broken_meta_event:
        for relation in state.relations:
            event = relation.broken_event
            logger.debug(f"decomposed meta {meta_event.name}: {event}")
            yield event, copy.deepcopy(state)
    elif is_rel_created_meta_event:
        for relation in state.relations:
            event = relation.created_event
            logger.debug(f"decomposed meta {meta_event.name}: {event}")
            yield event, copy.deepcopy(state)
    else:
        raise RuntimeError(f"unknown meta-event {meta_event.name}")


def generate_startup_sequence(state_template: State):
    yield from chain(
        decompose_meta_event(
            _Event(ATTACH_ALL_STORAGES),
            copy.deepcopy(state_template),
        ),
        ((_Event("start"), copy.deepcopy(state_template)),),
        decompose_meta_event(
            _Event(CREATE_ALL_RELATIONS),
            copy.deepcopy(state_template),
        ),
        (
            (
                _Event(
                    (
                        "leader_elected"
                        if state_template.leader
                        else "leader_settings_changed"
                    ),
                ),
                copy.deepcopy(state_template),
            ),
            (_Event("config_changed"), copy.deepcopy(state_template)),
            (_Event("install"), copy.deepcopy(state_template)),
        ),
    )


def generate_teardown_sequence(state_template: State):
    yield from chain(
        decompose_meta_event(
            _Event(BREAK_ALL_RELATIONS),
            copy.deepcopy(state_template),
        ),
        decompose_meta_event(
            _Event(DETACH_ALL_STORAGES),
            copy.deepcopy(state_template),
        ),
        (
            (_Event("stop"), copy.deepcopy(state_template)),
            (_Event("remove"), copy.deepcopy(state_template)),
        ),
    )


def generate_builtin_sequences(template_states: Iterable[State]):
    for template_state in template_states:
        yield from chain(
            generate_startup_sequence(template_state),
            generate_teardown_sequence(template_state),
        )


def check_builtin_sequences(
    charm_type: Type["CharmType"],
    meta: Optional[Dict[str, Any]] = None,
    actions: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    template_state: State = None,
    pre_event: Optional[Callable[["CharmType"], None]] = None,
    post_event: Optional[Callable[["CharmType"], None]] = None,
) -> object:
    """Test that all the builtin startup and teardown events can fire without errors.

    This will play both scenarios with and without leadership, and raise any exceptions.

    This is a baseline check that in principle all charms (except specific use-cases perhaps),
    should pass out of the box.

    If you want to, you can inject more stringent state checks using the
    pre_event and post_event hooks.
    """

    template = template_state if template_state else State()
    out = []

    for event, state in generate_builtin_sequences(
        (
            dataclasses.replace(template, leader=True),
            dataclasses.replace(template, leader=False),
        ),
    ):
        ctx = Context(charm_type=charm_type, meta=meta, actions=actions, config=config)
        with ctx.manager(event, state=state) as mgr:
            if pre_event:
                pre_event(mgr.charm)
            out.append(mgr.run())
            if post_event:
                post_event(mgr.charm)
    return out
