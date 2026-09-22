"""Test-only dispatch for exercising both independent pipeline implementations."""
from importlib import import_module
from curation.training.runtime import config


def runtime(branch):
    if branch not in {'training', 'benchmark'}:
        raise ValueError(branch)
    return import_module('curation.' + branch + '.runtime')


def load_pipeline(branch):
    return runtime(branch).load_pipeline()


def notebook_path(branch):
    return runtime(branch).notebook_path()


def graph_version(branch):
    return runtime(branch).graph_version()
