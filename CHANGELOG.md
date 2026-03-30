# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Voice connection state-change and error logging in `recorder.js` for debugging connection issues.
- `sodium-native` ^5.1.0 dependency — required by `@discordjs/voice` as the voice encryption backend.

### Changed

- Upgraded `@discordjs/voice` from ^0.18.0 to ^0.19.2 to gain DAVE (Discord Audio & Video Encryption) E2EE support. Version 0.18.0 did not implement the DAVE protocol, causing the voice connection to loop between `signalling` and `connecting` states indefinitely and never reach `Ready`.

### Fixed

- Voice connection timeout ("Timed out waiting for voice connection to be ready") caused by two missing pieces:
  1. `@discordjs/voice` 0.18.0 had no DAVE protocol support — upgraded to 0.19.2 which bundles `@snazzah/davey` for the DAVE handshake.
  2. No encryption library was installed — added `sodium-native` so the voice connection can complete its crypto negotiation.
