"""Ordering subtasks into waves: everything in a wave depends only on earlier waves."""


class CycleError(ValueError):
    pass


def waves(subtasks):
    """subtasks: iterable of dicts with 'id' and 'depends_on'. Returns a list of lists of ids."""
    remaining = {s["id"]: set(s.get("depends_on") or []) for s in subtasks}
    done, result = set(), []
    while remaining:
        ready = sorted(i for i, deps in remaining.items() if deps <= done)
        if not ready:
            raise CycleError("subtask dependencies form a cycle: " + ", ".join(sorted(remaining)))
        result.append(ready)
        done.update(ready)
        for i in ready:
            del remaining[i]
    return result
