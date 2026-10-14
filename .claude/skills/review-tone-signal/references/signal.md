# Signal construction

Two specifications, both declared before any result is seen. Restored here on
2026-10-14 after the restructure left them only in
`archive/historical-harness/references/methodology.md` — the peer specification had
been agreed and then existed nowhere live, which is how an agreed decision quietly
stops being one.

## The two specifications

```
S1 (primary)   signal(Q)      = tone_within(Q) − baseline(Q)
S2 (peer)      signal_peer(Q) = signal(Q) − mean over peers j of signal_j(Q)
```

`baseline(Q)` is the company's own mean within-product tone over the prior four
quarters. S1 asks whether a company's tone moved against its own history. S2 asks
whether it moved against its *category's* history, which is a different question: if
every brand in a category sours in one quarter, S1 reads that as news about each
company and S2 reads it as news about none of them.

Both are preregistered, `specifications_tested` records 2, and **a result that holds on
S2 but not S1 is reported as exactly that** — never as "the signal works".

## What a peer contributes, and what it does not

**A peer contributes `signal_j(Q)` — a tone number. It never contributes revenue.**

This is the whole reason a private company can be a peer. The pipeline has two
independent data streams:

| stream | source | works for |
|---|---|---|
| tone | Amazon reviews | **any** company that sells on Amazon |
| revenue | SEC / national filings | **listed** companies only |

A peer only ever enters through the tone stream. Revenue is read for the company being
tested, and for no one else. So Red Bull having no quarterly filing does not matter:
its job is to say what *the energy-drink category* did that quarter, and its reviews
answer that completely.

Worked through:

- Celsius (NASDAQ:CELH) — tone **and** revenue. The company under test.
- Red Bull GmbH (private) — tone **only**. The control.
- Test: *does Celsius's tone, relative to Red Bull's, predict Celsius's revenue
  surprise?*
- Red Bull's revenue never appears in that sentence, and is never needed.

## The limits this creates, stated because they are easy to miss

1. **A group with no listed member can produce tone charts and never a test.** It has
   no revenue to predict. Such groups are legitimate — they validate the method and act
   as category controls — but they are instruments, not targets, and a run must never
   imply otherwise.
2. **A private peer still has to clear every review floor.** Its tone must be
   computable, so `amazon_presence` and the velocity band in `CONTRACTS.md` §1 apply to
   it unchanged. A control Amazon cannot see controls for nothing.
3. **A company with no surviving peer emits no `signal_peer`.** It is recorded as
   peerless and **stays in S1** — dropping it would run the two specifications on
   different samples, and a difference between them would then be partly a difference
   of population rather than of method.
4. **Peers are fixed at declaration** and never revised after tone is seen. A peer
   chosen once a result is known is not a control, it is a choice.
