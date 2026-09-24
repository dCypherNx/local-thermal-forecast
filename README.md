# Local Thermal Forecast

Custom integration for Home Assistant that combines numerical weather forecasts with the
temperature history measured at the installation itself.

Every selected temperature sensor gets its own `weather` forecast entity. External sensors
receive independent 24-hour calibration and model selection for each forecast horizon. ECMWF
IFS HRES, Open-Meteo Best Match, DWD ICON Global and NCEP GFS Global are collected in parallel
and scored separately against each external sensor. Every configured room sensor gets an
independent 12-hour thermal model using its own history, the hybrid outdoor forecasts and solar
radiation. There is no integration-defined limit on either sensor list.

## Current scope

- UI-only Config Flow, Reconfigure Flow and Options Flow.
- Multi-model Open-Meteo collection with no credentials for non-commercial use.
- Explainable online recursive regression; no opaque machine-learning dependency.
- One `WeatherEntity` per selected sensor, with a 24-hour external or 12-hour internal series
  through Home Assistant's forecast API.
- Forecast ledger, observations used for verification, learned parameters and metrics restored
  after restart.
- Compact validation metrics on each forecast entity, without diagnostic entity explosion.
- Graceful fallback to raw weather data when local observations are unavailable.

This is an experimental forecast. It must not be used as a safety system or as the sole source
for severe-weather decisions.

The product has four forecast types. This release implements the first two without pretending
that the other two already exist:

| Type | Horizon | Status in 0.3.2 |
| --- | --- | --- |
| External temperature at home | 24 hours | Implemented, one calibrated forecast per selected sensor |
| Temperature of each room | 12 hours | Implemented, one independent thermal forecast per selected sensor |
| House-to-office regional weather | 24 hours | Planned |
| Arbitrary locations | 7 / 15 / 30 days | Planned with explicitly different confidence semantics by horizon |

## Provider assignment in this release

| Purpose | Provider / model |
| --- | --- |
| Weather API gateway for types 1 and 2 | Open-Meteo |
| Each external sensor | Independent adaptive choice among ECMWF IFS HRES, Open-Meteo Best Match, DWD ICON Global and NCEP GFS Global, then locally calibrated |
| Each internal sensor | Independent local thermal model driven by its history and the aggregate of the calibrated outdoor forecasts |
| Regional type 3 | Open-Meteo baseline; a future São Paulo radar/nowcasting and alerts source will be a separate provider |
| Arbitrary locations type 4, days 1–7 | Open-Meteo detailed forecast baseline |
| Arbitrary locations type 4, days 8–15 | Future ensemble/probabilistic provider; not represented as deterministic daily precision |
| Arbitrary locations type 4, days 16–30 | Future sub-seasonal/climatological provider; not represented as a conventional daily forecast |

Provider identity is kept in every forecast snapshot, while the normalized forecast and hybrid
model layers do not depend on Home Assistant entities. Regional house-to-office weather,
additional locations, alerts, nowcasting and 15/30-day products are deliberately outside this
first release.

## Installation

### HACS custom repository

1. Add this GitHub repository to HACS as an **Integration**.
2. Install **Local Thermal Forecast**.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and search for the integration.

### Manual

Copy `custom_components/local_thermal_forecast` to the same path below the Home Assistant
configuration directory and restart Home Assistant.

## Configuration

The setup flow asks for:

- the home coordinate, initially populated from Home Assistant;
- one or more outdoor temperature sensors;
- optional room temperature sensors.

The selectors accept any number of entities. A source cannot be both external and internal.
Each selected entity creates one forecast entity;
forecast points are served by Home Assistant's forecast API and are not copied into sensor state
attributes.

`weather` is used because it is Home Assistant's native entity with a forecast API. Every forecast
entity exposes the live absolute temperature from its configured source sensor. When that source
belongs to a Home Assistant device with exactly one humidity sensor, live humidity is exposed from
that sensor as well. Indoor forecasts keep a neutral thermometer icon and reuse the home's ambient
outdoor condition only to satisfy the WeatherEntity condition contract and allow the standard
frontend to render the live room temperature instead of `unknown`. Forecast temperatures remain
model outputs and are served separately through the forecast API.

The integration stores forecast snapshots, the later observations and errors associated with
those forecasts, and learned model parameters. General sensor history remains owned by Recorder.
Retention and update cadence are available under integration options.

## Data provenance

Open-Meteo is the API gateway. Every issued forecast retains the scientific model identifier.
The integration does not silently replace the selected home model when the API is unavailable;
it keeps the last valid forecast and exposes its issue timestamp.

## Development

Pure model and normalization tests can be run without a Home Assistant installation:

```bash
python -m unittest discover -s tests -v
```

Home Assistant validation should additionally run Hassfest and the HACS Action before release.
