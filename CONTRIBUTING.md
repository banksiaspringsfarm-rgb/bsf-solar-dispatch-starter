# Contributing

This started as one farm's dispatcher. It gets better when people with different rigs —
lithium banks, boats, vans, AC-coupled inverters, other monitoring boxes — try it and
report back. All of that is welcome.

**Ideas and questions → [Discussions](../../discussions).** "Would it work with X", "what
about Y as a load", design suggestions.

**It broke → [Issues](../../issues).** Include: inverter/battery/plug models, which step
of `CLAUDE.md` you were on, and the literal error. Photos of the gear help.

**Code / docs → pull requests.** Fork, branch, PR. Keep one change per PR. For anything
touching the dispatcher flow (`node-red/`), say what you tested it on and for how long —
it switches real loads, and the safety envelope (SOC windows, night lockout, load caps)
is not up for loosening without a very good reason. Run `python3 install/deploy.py check`
and, if you have `node`, `python3 install/deploy.py flow --dry-run` before you push.

Steven (@banksiaspringsfarm-rgb) reviews and merges. By submitting a pull request you agree your
contribution is licensed under the repo's licence (`LICENSE.md`, PolyForm Noncommercial) and
that Steven may also relicense it as part of the project — that's what keeps a future
commercial edition possible without having to chase every contributor. Your name stays in
the git history.
