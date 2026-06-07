default: check

fmt:
    uv run ruff format src/ tests/

lint:
    uv run ruff check --fix src/ tests/

typecheck:
    uv run mypy --strict src/
    uv run pyright src/ tests/

test *args:
    uv run pytest {{args}}

check: fmt lint typecheck test

# Re-run tests with an HTML coverage report in htmlcov/.
coverage:
    uv run pytest --cov=freshcache --cov-report=html --cov-report=term-missing

# Mutation testing. Results saved to .mutmut-cache.
# Show a summary with `just mutmut-results` after a run completes.
mutmut:
    uv run mutmut run

mutmut-results:
    uv run mutmut results

# Check for API breakage against the installed (or published) version.
#   just api-check             # compares src tree to last tagged release
api-check:
    uv run griffe check freshcache

# ---------------------------------------------------------------------------
# Release: build + validate + publish
# ---------------------------------------------------------------------------
#
# Tokens live encrypted in ``secrets.enc.yaml`` (sops + age). The
# publish recipes decrypt on demand using your age key at
# ``~/.config/sops/age/keys.txt``. Generate one with ``age-keygen``,
# then ``sops secrets.enc.yaml`` to drop your real ``test_pypi_token``
# / ``pypi_token`` in.
#
# Override via ``UV_PUBLISH_TOKEN=... just publish`` if you want to
# bypass sops for a one-off.
#
# Recipes refuse to upload if `twine check` flags any metadata problem.

# Path to the sops age key. Override if your key lives elsewhere.
sops_age_key := env_var_or_default("SOPS_AGE_KEY_FILE", env_var("HOME") + "/.config/sops/age/keys.txt")
secrets_file := "secrets.enc.yaml"

# Clean ./dist
clean-dist:
    rm -rf dist

# Build wheel + sdist.
build: clean-dist
    uv build

# Metadata validation — same gate PyPI applies on upload.
check-dist:
    uv run twine check dist/*

# Decrypt one token field from secrets.enc.yaml. Used by the publish
# recipes; can also be called directly:
#   just _token test_pypi_token | pbcopy
_token field:
    @SOPS_AGE_KEY_FILE={{sops_age_key}} sops -d --extract '["{{field}}"]' {{secrets_file}}

# Publish to TestPyPI. Token decrypted from secrets.enc.yaml unless
# UV_PUBLISH_TOKEN is already set in the environment.
publish-test: check-dist
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
      UV_PUBLISH_TOKEN=$(just _token test_pypi_token)
      export UV_PUBLISH_TOKEN
    fi
    uv publish --publish-url https://test.pypi.org/legacy/ dist/*

# Publish to real PyPI. Token decrypted from secrets.enc.yaml unless
# UV_PUBLISH_TOKEN is already set in the environment.
publish: check-dist
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
      UV_PUBLISH_TOKEN=$(just _token pypi_token)
      export UV_PUBLISH_TOKEN
    fi
    uv publish dist/*

# Read the package version from pyproject.toml.
_version:
    @python3 -c "import tomllib; print(tomllib.loads(open('pyproject.toml').read())['project']['version'])"

# Poll TestPyPI's simple index until freshcache=<version> is resolvable.
# Fails after ~5 minutes if the package never appears.
wait-indexed-test:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(just _version)
    echo "Polling TestPyPI simple index for freshcache==$version..."
    for i in $(seq 1 60); do
      if curl -sf "https://test.pypi.org/simple/freshcache/" 2>/dev/null \
           | grep -q "freshcache-${version}-"; then
        echo "✓ freshcache==$version indexed."
        exit 0
      fi
      printf "."
      sleep 5
    done
    echo
    echo "ERROR: freshcache==$version not on TestPyPI after 5 minutes." >&2
    exit 1

# Poll real PyPI's simple index until freshcache=<version> is resolvable.
wait-indexed:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(just _version)
    echo "Polling PyPI simple index for freshcache==$version..."
    for i in $(seq 1 60); do
      if curl -sf "https://pypi.org/simple/freshcache/" 2>/dev/null \
           | grep -q "freshcache-${version}-"; then
        echo "✓ freshcache==$version indexed."
        exit 0
      fi
      printf "."
      sleep 5
    done
    echo
    echo "ERROR: freshcache==$version not on PyPI after 5 minutes." >&2
    exit 1

# End-to-end: build, validate, publish to TestPyPI.
release-test: build check-dist publish-test

# End-to-end: build, validate, publish to PyPI.
release: build check-dist publish
