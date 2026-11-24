# Jarvis Panel contributor workflow

- Use Python 3.13 and follow the [public setup and test guide](docs/public-export.md).
- Run `python tools/check.py --tests-only` for the free offline checks.
- Work in `board_config/` and `styles/glass-v2/` when changing generated YAML.
  Keep generated files, build output and local configuration out of Git.
- Use the ESPHome SDL simulator for local UI verification.
