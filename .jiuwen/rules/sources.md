# Source Policy

Human-curated source policy for research runs. These rules are stable guidance,
not automatic output from source-health telemetry.

## Blocked Or Unreliable

- DuckDuckGo HTML and Lite search endpoints (`html.duckduckgo.com`, `lite.duckduckgo.com`): repeated empty or blocked results in worker traces.
- Brave search: unreliable for this workflow trace.
- Mojeek: unreliable for this workflow trace.
- Startpage: unreliable for this workflow trace.
- WeatherSpark: blocked or low-signal for the observed travel-weather lookup.
- Tripadvisor: blocked or low-signal for the observed hotel/travel lookup.
- Hotels.com: blocked or low-signal for the observed hotel/travel lookup.
- Expedia: blocked or low-signal for the observed hotel/travel lookup.

## Known Good

- holiday-weather.com: acceptable for climate and weather estimate context.
- budgetyourtrip.com: acceptable for travel budget estimates.
- rome2rio.com: acceptable for route and transport estimate context.

## Live OTA Fares

Live OTA fares are not reliably obtainable through the current research tools.
Use estimates with explicit confidence labels instead of presenting them as live
prices.
