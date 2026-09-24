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
- Diagnostic sensors for model age, selected source, bias and MAE.
- Graceful fallback to raw weather data when local observations are unavailable.

This is an experimental forecast. It must not be used as a safety system or as the sole source
for severe-weather decisions.

## Provider assignment in this release

| Purpose | Provider / model |
| --- | --- |
| Weather API gateway | Open-Meteo |
| Each external sensor | Independent adaptive choice among ECMWF IFS HRES, Open-Meteo Best Match, DWD ICON Global and NCEP GFS Global, then locally calibrated |
| Each internal sensor | Independent local thermal model driven by its history and the aggregate of the calibrated outdoor forecasts |

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

The selectors accept any number of entities. Each selected entity creates one forecast entity;
forecast points are served by Home Assistant's forecast API and are not copied into sensor state
attributes.

The integration stores forecast snapshots, the later observations and errors associated with
those forecasts, and learned model parameters. General sensor history remains owned by Recorder.
Retention and update cadence are available under integration options.

## Data provenance

Open-Meteo is the API gateway. Every issued forecast retains the scientific model identifier.
The integration does not silently replace the selected home model when the API is unavailable;
it keeps the last valid forecast and reports its age.

## Development

Pure model and normalization tests can be run without a Home Assistant installation:

```bash
python -m unittest discover -s tests -v
```

Home Assistant validation should additionally run Hassfest and the HACS Action before release.
