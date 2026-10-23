# Contributing to garmin-ble

garmin-ble is a small project and hasn't reached 1.0. The most useful
contributions are test reports from watches I don't own, and protocol features
from the [roadmap](./ROADMAP.md).

## Ways to help

- Test it on your watch. Run `python examples/full_walkthrough.py` and open an
  issue with the model, the firmware version, and the checklist it prints. We
  don't yet know which metrics work on which devices
  ([#23](https://github.com/gwerneckp/garmin-ble/issues/23)).
- Report bugs, ideally with a capture so someone without your watch can
  reproduce them. See [Reporting bugs](#reporting-bugs).
- Pick up an open item from the [roadmap](./ROADMAP.md). You don't need to ask
  first. See [Picking up roadmap work](#picking-up-roadmap-work).
- Suggest improvements, including changes to the current design. See
  [Proposing changes](#proposing-changes).
- Write documentation. The guides in the
  [Documentation milestone](https://github.com/gwerneckp/garmin-ble/milestones)
  (#29 to #31) haven't been started.

## Development setup

You need Python 3.10 or later.

```bash
git clone https://github.com/gwerneckp/garmin-ble.git
cd garmin-ble
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

You don't need a watch. The tests and most examples run against the in-process
simulator:

```python
async with Watch.simulated(profile="fenix7") as watch:  # also "venu3", "minimal"
    ...
```

```bash
python examples/telemetry_basic.py --simulate
```

CI runs `pytest` on Python 3.10 to 3.13 for every pull request, so run it
locally before you push.

## Project layout

| Path | Contents |
|------|----------|
| `garmin_ble/watch.py` | `Watch` and the session: connecting, handshake, heartbeat, reconnection, subscriptions |
| `garmin_ble/protocol/` | Wire formats: COBS, CRC16, GFDI framing, MLR multiplexing, the protobuf `Smart` envelope, Garmin binary JSON |
| `garmin_ble/metrics/` | One module per telemetry metric, each with a reading dataclass, a parser, and an encoder |
| `garmin_ble/transport/` | `ble` (real hardware through bleak), `simulated`, `replay` |
| `garmin_ble/protobuf/` | Generated `*_pb2.py` files. Don't edit these by hand. |
| `proto/` | Source `.proto` schemas, derived from Gadgetbridge |
| `tests/` | Same layout as the package |
| `examples/` | Runnable scripts and demos |
| `reverse_engineering/` | Notes and scripts from decoding undocumented formats |

## Adding a telemetry metric

Copy the structure of `garmin_ble/metrics/stress.py`.

1. Create `garmin_ble/metrics/<name>.py` with:
   - a module docstring giving the service code and the wire format
   - a frozen `@dataclass` that subclasses `Reading` and has named fields
   - `parse(data: bytes) -> Optional[YourReading]`, which returns `None` for a
     short or invalid payload instead of raising
   - `encode(reading) -> bytes`, the inverse of `parse`, used by the simulator
     and round-trip tests
   - a `register(...)` call with the name, `GarminService` code, class, parser,
     and encoder
2. Import it in `garmin_ble/metrics/__init__.py`, and add it to `__all__` and
   to `Metric.ALL_TELEMETRY`. The order of `ALL_TELEMETRY` is the display order.
3. Add a case for it in `_Vitals.next_reading` in
   `garmin_ble/transport/simulated.py` so the simulator can produce it.
4. Add parser tests to `tests/metrics/test_parsers.py`. Use bytes captured from
   a real watch if you have them.
5. Add it to the feature list in the README.

## Working with protobufs

The `.proto` files in `proto/` compile into `garmin_ble/protobuf/`. If you
change a schema, regenerate the Python code instead of editing it:

```bash
protoc --proto_path=proto --python_out=garmin_ble/protobuf proto/*.proto
```

The checked-in code was generated with protobuf 6.31.1. Commit the `.proto`
change and the regenerated `_pb2.py` in the same commit.

## Design rules

These follow from the [design philosophy](./README.md#design-philosophy) and
from bugs fixed in 0.3.1 and 0.3.2 (see the [changelog](./CHANGELOG.md)).
Reviews check for them.

- The library handles the wire protocol only. It encodes, decodes, and responds
  to protobufs. It doesn't call weather APIs, read calendars, or play sounds.
  Expose a callback or a builder and leave the integration to the caller.
- Don't add an API that looks like it answers a question the protocol can't
  answer. If a field is missing, return `None`. Don't fill in a plausible
  default.
- Fail where the failure happens. Raise a typed error from
  `garmin_ble/errors.py`, or emit an event. Don't log and carry on, and don't
  leave a stream that never yields.
- Every public name needs a caller in the library, tests, examples, or README.
- Everything must run without hardware. New protocol features need simulator
  support so CI can test them.

## Picking up roadmap work

You can pick up any open item on the [roadmap](./ROADMAP.md). Comment on the
issue to say you're working on it so nobody duplicates the work. If a claimed
issue has had no activity for a few weeks, ask in the issue and then take it
over.

Most of the remaining features need reverse engineering before any library code
can be written. You'll have to work out undocumented message layouts, how the
watch responds to each message, and how individual models differ. That work is
slow and uncertain, so:

- Explain your reasoning in the PR. Say why you did it this way, what else you
  tried, and what supports your choice (captures, Gadgetbridge code,
  experiments). "I used X because the watch rejected Y, see this capture" is
  easy to review.
- Write down what you couldn't work out. A field you couldn't decode, or
  behaviour you only saw on one model, is fine to leave open. Note it in the
  code or the PR and don't guess.
- Partial progress is worth submitting. A write-up under `reverse_engineering/`
  with captures and findings is a useful contribution even without library
  code. `reverse_engineering/accelerometer/` is an example.
- If you're unsure about the direction, open a draft PR early and ask.

## Proposing changes

I'm open to suggestions that make the library better, including ones that
question existing design decisions or the order of the roadmap. Breaking API
changes are still possible before 1.0 if the reasons are good.

For anything bigger than a bug fix, open an issue first and cover:

- The problem: what is hard, wrong, or missing now, with a concrete example if
  possible.
- The proposal: what you would change, with a code sketch if it affects the
  API.
- The justification: why it's better, what it costs, which breaking changes it
  needs, and which alternatives you considered.

## Reverse engineering and sources

The protocol logic and schemas are based on
[Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge), which is
AGPL-3.0 like this project. New protocol work can use:

- Gadgetbridge and other open-source projects with compatible licenses (name
  them in the PR)
- BLE captures from hardware you own
- your own experiments, preferably written up under `reverse_engineering/`

Don't contribute code or constants copied from decompiled Garmin apps or
firmware. I'd find it as interesting as you would, but I can't afford to take
on Garmin's lawyers, so we stay in the clean room.

## Reporting bugs

Include:

- the watch model and firmware version (`watch.info` has both)
- your OS, Python version, and `garmin-ble` version
- the smallest script that reproduces the problem
- logs with debug logging turned on
- a capture, if you can:

  ```python
  watch.record("bug.gble")
  ```

  Anyone can replay it with `Watch.replay("bug.gble")`.

> [!WARNING]
> A capture contains your health data (heart rate, stress, SpO2 and so on) and
> details about your watch. Only attach one you're happy to make public.

If discovery fails, run `python examples/scan.py` first. The usual cause is that
the watch is still connected to your phone.

## AI coding assistants

Use whatever tools you like. Most people write code with an AI assistant now,
and I don't care which agent, model, or harness you used. What matters is the
code. A PR is held to the same standard whether you typed every line or none of
them.

- Don't add `Assisted-by`, `Co-authored-by`, or similar trailers to credit an
  AI tool. If your tool adds one automatically, remove it.
- You are responsible for what you submit. Review and understand every line,
  and be able to explain it in review. "The AI wrote it" doesn't answer a
  review question.
- AI agents must not add `Signed-off-by`. Only a person can certify the
  [Developer Certificate of Origin](#developer-certificate-of-origin), and your
  sign-off means you take responsibility for the change, including its
  licensing.
- The licensing rules apply to generated code too. It must not reproduce code
  under a license incompatible with AGPL-3.0, or code from the sources excluded
  in [Reverse engineering and sources](#reverse-engineering-and-sources).
- Back up protocol details an AI suggests with a capture, a test, or a cited
  source. Models will describe byte layouts they have never seen.

## Developer Certificate of Origin

Every commit needs a `Signed-off-by` line certifying the
[Developer Certificate of Origin](https://developercertificate.org/), which
says you wrote the change or otherwise have the right to submit it under this
project's license. `git commit -s` adds the line. Use your real name.

## Pull requests

- One change per PR. Keep refactors and features in separate PRs.
- Sign off every commit (`git commit -s`).
- Explain in the description why you made the change as well as what it does.
  For protocol work, link the captures or sources your decisions are based on.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`. Mark breaking
  changes with `!` (`refactor!: ...`) and reference issues (`feat: #13 ...`).
- Add an entry under `## [Unreleased]` in `CHANGELOG.md` for anything a user
  would notice. Breaking changes need a before and after migration example.
- New behaviour needs tests. A bug fix needs a test that failed before the fix.
- Follow the existing style: type hints, `from __future__ import annotations`,
  and docstrings that explain why the code does what it does.

## Releases (maintainers)

Bumping `version` in `pyproject.toml` on `main` runs
`.github/workflows/publish.yml`. It tags `vX.Y.Z`, creates a GitHub release,
and publishes to PyPI. Move the `[Unreleased]` changelog entries under the new
version before you bump.

## License

Contributions are licensed under the [AGPL-3.0](./LICENSE).
