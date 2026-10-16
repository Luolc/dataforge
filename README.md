# dataforge

dataforge is the shared part of a data machine: a small workshop where coding agents build datasets, each dataset in its own repo. It sets up the machine. Claude Code is currently the only supported agent.

- [`docs/design.md`](docs/design.md): what dataforge is, how its parts fit together, and the invariants the tests guard.
- [`machine/`](machine/): the data machine setup; [`machine/README.md`](machine/README.md) is the manual for a new machine.
- `machine/agent/`: the files every agent on a data machine gets, installed by the setup once they exist; there are none yet.
- [`AGENTS.md`](AGENTS.md): rules for agents working on this repo, including the local checks.

## License

dataforge is licensed under the Apache License, Version 2.0; see [`LICENSE`](LICENSE).
