# Mutation testing platform notes

Graphlink's native development target is Windows with Python **3.12+**.
The normal contributor extras are cross-platform:

```bash
python -m pip install -e ".[dev]"
```

They provide pytest, coverage, xdist, ruff, mypy and Hypothesis.

**Mutmut 3.x is different:** it uses Unix `os.fork()`, which does not
exist in native Windows Python. Running `mutmut run` on Windows therefore
does not work. Do not add it back into the `dev` extra and promise that
every declared dev tool runs there.

Run mutation testing in Linux or WSL2 with Python 3.12+:

```bash
python -m pip install -e ".[dev,mutation]"
mutmut run
```

The `mutation` extra applies only when `sys_platform != 'win32'`.
The existing `[tool.mutmut]` configuration targets four small modules;
see `.github/workflows/nightly-mutation.yml` for the scheduled Ubuntu
job. Avoid running mutation tests on Windows outside WSL.
