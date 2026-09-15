"""mp-agent: a multipass, multi-agent coding loop.

A worker model does the work, many times over. Deterministic checks, independent
reviewers and a final judge decide when it is done, never the worker. For larger
tasks a planner splits the work into subtasks that run in parallel. Every role
can be any model a supported command-line tool can reach.
"""
__version__ = "2.0.0"
