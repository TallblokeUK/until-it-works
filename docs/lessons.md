# Lessons from the first version

Until It Works(hop) began as a bash loop around one fast model (Mercury 2.5 through Cline). That version
is retired, but what it measured shaped the design, so it is kept here.

## Racing candidates (removed)

    mp-agent --candidates 3 "Implement wrap() so the tests pass"

Three agents attempt the same task at once in separate worktrees. Passing the
check is mandatory; among those that pass, the smallest change wins, because
the smallest change that satisfies the same tests has the least room for an
accident in it. Losing worktrees are removed and their diffs kept. The winner
carries on into the normal review and repair loop.

Only the first pass is raced. After that there is one line of work, because
repairing a specific failure is not a task that benefits from three opinions.

**The provider limits this before the model does.** Mercury answers in seconds
but meters input tokens per minute, and an agent reading a repository spends
them in gulps. Three candidates at once produced "Rate limit reached: input
token limit exceeded" and three candidates that had changed nothing.

Every call to a provider now goes through one queue that will not let two
starts be closer together than `MP_MIN_INTERVAL` (20 seconds by default). The
lock is held across the wait, so concurrent candidates take turns rather than
all waking at once, and the pacing holds between separate runs as well as
within one. A call that is refused anyway waits a full minute, not less, since
the window is a minute wide and a shorter wait only spends another call finding
that out; it also widens the queue's spacing, on the grounds that being refused
is evidence the current pace is too fast for what these prompts weigh.

Measured: three candidates on `to_roman()`, one refused, waited, retried and
finished. All three passed, and the race took 80 seconds rather than losing a
candidate.

## What has been measured

Four tasks on a small Python repository, Mercury 2.5 through Cline 3.0.61:

| Task | Time | Calls | Outcome |
|---|---|---|---|
| Return 0 for the mean of an empty list | 18s | 2 | approved first pass |
| Implement slugify against seven tests | 74s | 5 | check failed, repaired, reviewer objected, repaired, approved |
| The same, with the reviewer's rule tightened | 20s | 2 | approved first pass |
| Fold characters NFKD cannot decompose | 28s | 2 | approved first pass |
| Add a guard to a real 30k-line repository | 414s | 2 | approved first pass, comment copied from the wrong file |
| Implement wrap(), three candidates raced | 62s | 4 | all three passed, smallest won, approved |
| Implement split_bill(), two raced, DeepSeek V4 Pro audit | 177s | 5 | fast reviewer approved; DeepSeek refused; repaired; approved |
| Implement to_roman(), three raced | 139s | 4 | one candidate rate-limited, waited, retried; all three passed |

The 74 second run is the interesting one: it is the whole hypothesis in one
place. A check caught a real failure, the exact output was fed back, the model
repaired it, an independent reviewer then objected, and a further pass settled
it. Three passes and two reviews took barely over a minute.

## Why the final gate earns its cost

On `split_bill()` the checks passed and the fast reviewer approved. DeepSeek
refused, and it was right: the implementation divided by `people` with no
guard, so `split_bill(1000, 0)` raised `ZeroDivisionError`. No test covered it,
so nothing deterministic could have caught it. What made the objection worth
having was that it had read the rest of the repository: it pointed out that
`average([])` returns `0` and `wrap("")` returns `[]`, so this was the one
function that raised on its degenerate case, against the codebase's own
convention. Mercury repaired it and the second audit approved, while still
noting as remarks, not blockers, that returning `[]` for zero people arguably
loses the money.

That is the whole argument in one run: deterministic checks catch what they
were written to catch, a cheap reviewer catches the obvious, and the expensive
one catches what nobody thought to test.

## Why the audit stays at the end

An earlier draft of this document suggested escalating to the stronger model
early when a change touches authentication, payments, migrations or
cryptography. That was wrong and it is worth saying why.

The strong model is a gate, not a co-author. Whatever the fast loop produces
has to pass the same checks and the same audit regardless of what it touches,
so consulting the expensive model earlier adds no safety: it only spends money
reviewing drafts that are about to be rewritten anyway. If the worry is that a
sensitive change might slip past, the answer is a stricter gate at the end, not
an earlier one, because the end is the only point where what is being judged is
what would actually be kept.
