#!/bin/bash

uv run pytest -W ignore::DeprecationWarning \
  --cov=gee_toolbox \
  --cov-report=term-missing \
  --cov-report=xml \
  --cov-report=html \
  tests/
