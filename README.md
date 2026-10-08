# WatchNPlay for Kodi

Kodi add-on (`service.watchnplay`) that keeps your [WatchNPlay](https://watch-n-play.com) account up to date with what you watch in Kodi.

WatchNPlay is an app for iPhone and Android and a website at [watch-n-play.com](https://watch-n-play.com): track movies, series and games, rate them and see where they are streaming.

Works with Kodi 19 (Matrix) and newer on every platform: Android TV / Google TV, phones and tablets, Windows, macOS, Linux, LibreELEC and CoreELEC. Pure Python 3, no third-party libraries.

## Features

- **Watched sync**: movies and episodes that Kodi marks as watched are added to your WatchNPlay history, with the time you watched them. Playback you stop early is ignored, exactly like Kodi does.
- **Streaming add-ons**: playback of movies and episodes from video add-ons counts once it is almost finished (Kodi's own watched threshold, 90 % by default). Titles with a TMDB, IMDb or TVDB id are booked directly, titles without an id go to a review list in WatchNPlay.
- **Back-sync (optional)**: titles you mark as watched or unwatched in WatchNPlay are marked in Kodi too, including titles that join your Kodi library later.
- **Easy pairing**: the add-on shows a QR code and a short code on your TV. Scan it with your phone or enter the code in the WatchNPlay app or on [watch-n-play.com/kodi](https://watch-n-play.com/kodi). There is nothing to type on the TV.

Syncing requires WatchNPlay Pro. Not affiliated with the Kodi project or any streaming service.

## Install

Once the add-on is in the official Kodi repository: *Add-ons › Install from repository › Kodi Add-on repository › Services › WatchNPlay*.

Until then, download the zip from [api.watch-n-play.com/kodi](https://api.watch-n-play.com/kodi/) and use *Add-ons › Install from zip file* (allow unknown sources first).

## Privacy

The add-on only sends what WatchNPlay needs to match your titles: for movies and episodes in your Kodi library and for finished streaming playback the title, year, season and episode number, TMDB/IMDb/TVDB ids, watched state and the time you watched, plus the id of the streaming add-on (for example plugin.video.netflix). When pairing it sends the device name, platform, Kodi version and add-on version so you can tell your devices apart. File paths and stream addresses never leave your device. The device token is stored in Kodi's add-on data folder and never written to the log. Disconnecting in the add-on or in WatchNPlay removes the device.

## Development

```
python tests/test_logic.py      # offline tests, Kodi modules are stubbed
python build_zip.py             # -> dist/service.watchnplay-<version>.zip
```

Logs appear in `kodi.log` with the prefix `[service.watchnplay]`.

## License

[MIT](LICENSE)
