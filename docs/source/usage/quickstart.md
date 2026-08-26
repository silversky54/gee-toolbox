# Quickstart

Initialize Earth Engine, then use helpers from `gee_toolbox`:

```python
import ee
import gee_toolbox.assets as assets
from gee_toolbox.dates import ee_date_to_datetime

ee.Initialize()

# List assets under a folder (names only)
names = assets.list_assets(
    "projects/your-project/assets/your-folder",
    names_only=True,
)

# Convert an ee.Date (or epoch ms) to a Python datetime
info = ee.Date("2024-01-01")
dt = ee_date_to_datetime(info)
```

See the API reference for full signatures and options.
