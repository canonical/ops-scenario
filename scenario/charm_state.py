#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import inspect
from typing import Generic, TypeVar

_M = TypeVar("_M")


class CharmStateBackend(Generic[_M]):
    # todo consider alternative names:
    #  - interface?
    #  - facade?

    def _generate_model(self):
        """use inspect to find all props and generate a model data structure."""
        model = {}
        prop: property
        # todo exclude members from base class
        for name, prop in inspect.getmembers(
            type(self),
            predicate=lambda o: isinstance(o, property),
        ):
            settable = bool(getattr(prop, "fset", False))
            model[name] = settable

        return model
