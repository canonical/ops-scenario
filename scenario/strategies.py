#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""This module contains Hypothesis strategies for property-based testing of charm code,
based on State templates.

In other words, a Hypothesis plugin enabling you to generate, in principle,
all possible states, contexts and events a charm might be called with.
"""
from functools import partial
from typing import TYPE_CHECKING, Any, Dict, Optional, Type

import pydantic
from hypothesis import strategies as st

from scenario import Context
from scenario import state as scenario_state
from scenario.context import DEFAULT_JUJU_VERSION

if TYPE_CHECKING:
    from scenario.context import PathLike


class Plugin:
    def __init__(
        self,
        charm_type: Type["scenario_state.CharmType"],
        meta: Optional[Dict[str, Any]] = None,
        actions: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        charm_root: Optional["PathLike"] = None,
        juju_version: str = DEFAULT_JUJU_VERSION,
        capture_deferred_events: bool = False,
        capture_framework_events: bool = False,
    ):
        self._charm_type = charm_type
        self._meta = meta
        self._actions = actions
        self._config = config
        self._charm_root = charm_root
        self._juju_version = juju_version
        self._capture_deferred_events = capture_deferred_events
        self._capture_framework_events = capture_framework_events

    @property
    def context(self):
        """Context strategy fuzzing around with app name and unit ID."""
        return st.builds(
            partial(
                Context,
                charm_type=self._charm_type,
                meta=self._meta,
                actions=self._actions,
                config=self._config,
                charm_root=self._charm_root,
                juju_version=self._juju_version,
                capture_deferred_events=self._capture_deferred_events,
                capture_framework_events=self._capture_framework_events,
            ),
            app_name=st.text(),
            unit_id=st.integers(),
        )

    def databags(self, model: Optional[pydantic.BaseModel] = None):
        """Strategy fuzzing about with possible databags."""
        if not model:
            return st.dictionaries(keys=st.text(), values=st.text())
        return st.builds(model)

    def _relations_over_endpoint(
        self,
        endpoint: str,
        meta: Dict[str, Any],
        peer: bool = False,
    ):
        base_strategies = {
            "local_unit_data": self.databags(),
            "local_app_data": self.databags(),
            "relation_id": st.integers(),
        }
        if peer:
            return st.builds(
                partial(
                    scenario_state.PeerRelation,
                    endpoint,
                    interface=meta["interface"],
                ),
                peers_data=st.dictionaries(keys=st.integers(), values=self.databags()),
                **base_strategies,
            )

        else:
            if meta.get("scope") == "container":
                return st.builds(
                    partial(
                        scenario_state.SubordinateRelation,
                        endpoint,
                        interface=meta["interface"],
                    ),
                    remote_app_name=st.text(),
                    remote_app_data=self.databags(),
                    remote_unit_data=st.dictionaries(
                        keys=st.integers(),
                        values=self.databags(),
                    ),
                    **base_strategies,
                )
            else:
                return st.builds(
                    partial(
                        scenario_state.Relation,
                        endpoint,
                        interface=meta["interface"],
                        limit=meta.get("limit", 1),
                    ),
                    remote_app_name=st.text(),
                    remote_app_data=self.databags(),
                    remote_units_data=st.dictionaries(
                        keys=st.text(),
                        values=self.databags(),
                    ),
                    **base_strategies,
                )

    @property
    def relations(self):
        """Strategy fuzzing about with the possible relations and their databag values."""
        meta = self._meta
        possible_relations = []

        for key in ["requires", "provides"]:
            for endpoint, relation_meta in meta.get(key, {}).items():
                rel_st = self._relations_over_endpoint(
                    endpoint,
                    relation_meta,
                    peer=False,
                )
                possible_relations.append(rel_st)

        for endpoint, relation_meta in meta.get("peers", {}).items():
            rel_st = self._relations_over_endpoint(endpoint, relation_meta, peer=True)
            possible_relations.append(rel_st)

        return st.sampled_from(possible_relations)

    @property
    def configs(self):
        """Strategy fuzzing about with the possible config values."""
        if not self._config:
            # dummy strategy only generating a single empty dict
            return st.fixed_dictionaries({})

        _type_to_strategy = {
            "string": st.text(),
            "int": st.integers(),
            "float": st.floats(),
            "boolean": st.booleans(),
        }
        template = {}

        for key, option_meta in self._config.get("options", {}).items():
            template[key] = _type_to_strategy[option_meta["type"]]

        return st.fixed_dictionaries(template)

    @property
    def states(self):
        return st.builds(
            scenario_state.State,
            config=self.configs,
            relations=self.relations,
            # networks=self.networks,
            # containers=self.containers,
            # storage=self.storages,
            # opened_ports=self.opened_ports,
            # leader=self.leader,
            # model=self.models,
            # secrets=self.secrets,
            # resources=self.resources,
            # planned_units=self.planned_units,
            # deferred=self.deferred_events,
            # stored_state=self.stored_states,
            # app_status=self.app_statuses,
            # unit_status=self.unit_statuses,
            # workload_version=self.workload_versions,
        )

    def events(self):
        """Strategy generating all possible events that can be emitted on this charm."""
