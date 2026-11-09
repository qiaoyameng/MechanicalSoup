# Reproducible authoring environment

Upstream: https://github.com/MechanicalSoup/MechanicalSoup
Upstream commit: 385962b0b091b7dc99938435874d4651c7cc7d04
Verified runtime: 3.13.14 (tags/v3.13.14:fd17997, Jun 10 2026, 13:03:48) [MSC v.1944 64 bit (AMD64)]
Verified platform: win32

Use an isolated virtual environment. Install the pinned dependencies with
`python -m pip install -r requirements-domain.lock`, then `python -m pip install -e .`.
Set the following environment variables before installation and testing:

```text
PYTHONUTF8=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
```

Core baseline:

```text
python -m pytest -p pytest_mock -p requests_mock.contrib._pytest_plugin -q -o addopts= tests/test_form.py -k "not test_submit_online and not test_submit_set"
```

Measured pytest process time: 0.486 seconds on the authoring host.
The selected form suite excludes two httpbin fixture tests. No browser process or web server is required for this offline baseline.

All documented checks use in-memory fixtures or local temporary files. No production
accounts, data services, credentials or external network endpoints are required after setup.
Reset by making a fresh checkout of this seed and creating a new virtual environment.
Test fixtures remain under their upstream repository license.
