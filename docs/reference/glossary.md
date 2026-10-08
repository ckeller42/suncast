# Glossary

```{glossary}
:sorted:

Bulk hour
  An hour whose mean `charge_state` is at least 2.5 and below 3.5. The MPPT is not throttled, so
  the absorbed power reflects what the panel can deliver.

buspi
  The Raspberry Pi in the camper that hosts the services.

Calibration factor
  Median of the daily actual/forecast ratios, clamped, applied to the primary forecast.

ERA5
  Reanalysis of past weather, used only by the offline tools.

GTI
  Global tilted irradiance in W/m2, Open-Meteo's panel-plane irradiance.

MPPT
  Maximum power point tracker, the Victron solar charger.

Ratio
  `actual_wh / forecast_wh` over the bulk hours of one day.

Snapshot
  A stored forecast (hourly and daily series, location, panel) taken once per UTC day.
```
