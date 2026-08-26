# Task runner for gee-toolbox
# https://github.com/casey/just

set shell := ["bash", "-cu"]

package := "gee-toolbox"

default:
    @just --list

# Install/sync project dependencies
sync:
    uv sync

# Run tests with coverage
test:
    uv run pytest -W ignore::DeprecationWarning \
        --cov=gee_toolbox \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-report=html \
        tests/

# Remove previous Sphinx output (avoids stale HTML/doctrees after renames/deletes)
docs-clean:
    rm -rf docs/build

# Build HTML docs into docs/build/html
docs: docs-clean
    uv run sphinx-build -b html docs/source docs/build/html

# Build docs and treat warnings as errors
docs-strict: docs-clean
    uv run sphinx-build -b html docs/source docs/build/html -W

# Print the next version without changing files
bump-print:
    uv run semantic-release version --print

# Stamp the next version into project files (no commit)
[private]
bump-stamp:
    uv run semantic-release version --skip-build --no-commit --no-tag --no-changelog

# Refresh uv.lock after the version stamp
[private]
bump-lock: bump-stamp
    uv lock --upgrade-package {{package}}
    git add uv.lock

# Create the release commit and tag locally (no push)
[private]
bump-release: bump-lock
    uv run semantic-release version --skip-build --no-push

# Build wheel/sdist and publish to PyPI
[private]
bump-publish:
    uv sync
    uv build
    uv publish

# Full release: bump version, update lockfile, commit, build, publish (no git push)
bump: bump-release bump-publish
