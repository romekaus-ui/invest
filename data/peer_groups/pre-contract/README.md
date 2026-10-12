# Pre-contract groups — not declarations

Three groups declared on 2026-10-11, while the peer-group component was being built and
**before `CONTRACTS.md` and PLAN.md's concentration filter existed**. They were the
component's own end-to-end test: every ASIN in them was resolved live, and the two
defects that live run exposed are recorded in `CHANGELOG.md`.

They are kept, not deleted, because they are the evidence that the component was tested
against real data. They are **not declarations of record** and the workflow must not read
them:

- They use the pre-contract schema (`listing` rather than `venue`, `title_at_declare`
  rather than `name`, a `{kind, value}` seed, a timestamp rather than a date), so they
  cannot satisfy `CONTRACTS.md` §1.
- They carry **no concentration evidence**, because the filter did not exist yet. Under
  it most of their members do not qualify — which is the finding, recorded in
  `CHANGELOG.md` under "the concentration filter is the binding constraint".

Nothing was edited in place to produce this directory; the files are byte-identical to
what was frozen, hashes included. `declare_group.py verify` skips them because they
predate `schema_version: 1`.
