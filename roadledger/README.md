# RoadLedger — original offline-first Android vehicle log

A clean-room native Android MVP designed from general architectural lessons in a vehicle/fuel-tracking application audit. **No Fuelio source code, assets, package names, API credentials, or branded designs are included.**

## Features in v0.1

- Manage multiple vehicle profiles (vehicle name, make, model)
- Enter fuel fill-ups with date, odometer (km), liters, total cost, and full/partial status
- Compute weighted L/100 km from complete **full-to-full** intervals, counting partial fills between anchors
- Track maintenance, insurance, parking, tolls, repairs, and other expenses
- Manually record trips with start/end odometer readings
- See an offline dashboard with economy, spending, trip totals, and recent fill-ups
- Export all records via Android's document picker to a CSV you own
- Long-press any record to delete (vehicle deletion confirms and cascades to records)

## Limitations

**Prototype, not production-ready.** It does not provide edit-in-place, live GPS/background tracking, Bluetooth automation, cloud sync, station API, receipt OCR, Android Auto, currency conversion, notifications, automatic reminders, or billing. Trips are manually entered. The app requests **zero Android permissions**. Monetary values are USD, distances km, fuel liters; these fixed units are shown in the UI.

Fuel economy is only calculated between two full fill-ups at distinct increasing odometer readings. Intermediate partial fills are counted; an incomplete interval has no economy value. This is a new, simplified implementation, not a reproduction of any proprietary algorithm.

The local SQLite database uses app-private storage and **is not additionally encrypted**. Android device backup is disabled. CSV export is manually initiated, and a user-created CSV is not encrypted by the app.

## Build an APK in Android Studio

1. Install Android Studio and Android SDK Platform 35.
2. Open this `roadledger/` folder as a Gradle project.
3. Use JDK 17 and Gradle 8.9 (Gradle sync may prompt to install it).
4. Select **Build → Build Bundle(s) / APK(s) → Build APK(s)**, or run:
   
   ```bash
   cd roadledger
   gradle :app:assembleDebug
   ```
5. Find **`app/build/outputs/apk/debug/app-debug.apk`** and install it on Android 8.0+.

The APK is **debug-signed**, for personal testing only, and is not suitable for Play Store distribution.

## Optional GitHub Actions build

The CREW bridge correctly requires separate human review before updating executable workflow files. A **non-active workflow template** lives at `ci/roadledger-apk.workflow.yml`. After reviewing it, a maintainer can manually place it at `.github/workflows/roadledger-apk.yml` and trigger the build on GitHub. **The template is not active and no APK is generated merely by opening this PR.**

## Privacy and provenance

No `INTERNET`, `ACCESS_FINE_LOCATION`, tracking, ad SDK, or analytics permissions. RoadLedger is independently implemented and should be tested with synthetic/non-sensitive sample data before personal use.
