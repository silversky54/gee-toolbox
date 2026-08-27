# CHANGELOG

<!-- version list -->

## v0.4.1 (2026-08-26)

### Bug Fixes

- Fix various ExportTask and ExportTaskList status mismatch that prevented Tasks from updating their
  status
  ([`e87ab3b`](https://github.com/silversky54/gee-toolbox/commit/e87ab3b7218f9b08ca2b1fc8fd97b3f3ed6b5fa2))


## v0.4.0 (2026-08-26)

### Bug Fixes

- Fix reading state into ExportTask from a GEE Task. GEE Tasks use Enum.
  ([`2079a59`](https://github.com/silversky54/gee-toolbox/commit/2079a59c657fc7cbd82f38ce1aefe0677ba8d26d))

### Features

- Add method ExportTaskList.start_tasks as alias for start_exports for naming clarity.
  ([`bcd757a`](https://github.com/silversky54/gee-toolbox/commit/bcd757a845920a669cd1716f5b26566c132bea8e))


## v0.3.0 (2026-08-26)

### Features

- Add Classes and functions to manage EE Tasks. gee-toolbox.batch.tasks.exports
  ([`3ea1907`](https://github.com/silversky54/gee-toolbox/commit/3ea190766c539688c50d20e2faf9ecd044b0ffab))

- Add module "dates" with functions to manage dates within GEE or to convert between GEE and local
  python.
  ([`396930c`](https://github.com/silversky54/gee-toolbox/commit/396930ccb72e1519c225ee3b4e8ed44496abf4be))

- Add new module "images" with functions to add properties to Images
  ([`f81a4b5`](https://github.com/silversky54/gee-toolbox/commit/f81a4b5d3ee4642cdcc0bbe563d327f080a5c65f))

- Add new module 'image_collections' with functions to filterer and add properties to
  ImageCollections.
  ([`5057eb0`](https://github.com/silversky54/gee-toolbox/commit/5057eb09cfaa9c5b6b1df7b399b805b1d9986d73))

- Deprecated functions in module 'gee.assets' are being substituted with new versions in 'assets'
  ([`39167fc`](https://github.com/silversky54/gee-toolbox/commit/39167fcf7161c094b999866c083d0dd4907171a0))

## v0.2.1 (2025-01-05)

### Build System

- Removed geetools dependency
  ([`18adca9`](https://github.com/silversky54/gee-toolbox/commit/18adca927c32214e0ddcb9d41f8fb0287aab6ad7))

### Refactoring

- Removed dependency of geetools. geetools had bloated in size and had dependency errors.
  ([`e81ddb1`](https://github.com/silversky54/gee-toolbox/commit/e81ddb1796a82cd60de03a4214af83867901879e))

## v0.2.0

Minimum version of geetools is now 1.10.0
Several improvements to list_assets and prune functions
