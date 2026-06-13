# Changelog

## [0.16.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.0...orchestrator-v0.16.1) (2026-06-13)


### Bug Fixes

* trace parents connections ([#154](https://github.com/rumankazi/runnerforge/issues/154)) ([ba57a65](https://github.com/rumankazi/runnerforge/commit/ba57a65fadf4c7cb7972edb1c97016f98682a29e))

## [0.16.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.15.0...orchestrator-v0.16.0) (2026-06-11)


### Features

* **orchestrator+infra:** strip GH-side delay from Time-to-Register SLO ([#146](https://github.com/rumankazi/runnerforge/issues/146)) ([4c13322](https://github.com/rumankazi/runnerforge/commit/4c13322acb0a48cee1b2a5c9ae9a0a683a13f167))

## [0.15.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.14.0...orchestrator-v0.15.0) (2026-06-11)


### Features

* add spot machines status cross check ([#133](https://github.com/rumankazi/runnerforge/issues/133)) ([29d8266](https://github.com/rumankazi/runnerforge/commit/29d82667deb8d8ad9c11ce1ffc610c39d6438bc9))

## [0.14.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.13.0...orchestrator-v0.14.0) (2026-06-11)


### Features

* **orchestrator:** labels processing and handling backend ([#130](https://github.com/rumankazi/runnerforge/issues/130)) ([43dc63f](https://github.com/rumankazi/runnerforge/commit/43dc63f500c769b51461d7a3bb3a1ceac859891f))
* wire up spot vm creations ([#132](https://github.com/rumankazi/runnerforge/issues/132)) ([e9fb010](https://github.com/rumankazi/runnerforge/commit/e9fb010d8e12b30c06bca584687a8481f136e112))

## [0.13.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.12.0...orchestrator-v0.13.0) (2026-06-10)


### Features

* **infra+orchestrator:** operational alerts + lost-webhook detection ([#120](https://github.com/rumankazi/runnerforge/issues/120)) ([0190f35](https://github.com/rumankazi/runnerforge/commit/0190f35c8ffaa3c1ac500ec866b1f7b9037311b0))

## [0.12.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.11.0...orchestrator-v0.12.0) (2026-06-10)


### Features

* **orchestrator:** codify observability in Terraform ([#113](https://github.com/rumankazi/runnerforge/issues/113)) ([4d13d95](https://github.com/rumankazi/runnerforge/commit/4d13d95d4c10c6a38b818b928ead785e5bc1d000))


### Bug Fixes

* **orchestrator:** qualify resolved runner image as a project-scoped path ([#114](https://github.com/rumankazi/runnerforge/issues/114)) ([192d54a](https://github.com/rumankazi/runnerforge/commit/192d54a9aecc4d4368eedfb5e2e841744390af69))

## [0.11.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.10.0...orchestrator-v0.11.0) (2026-06-10)


### Features

* **orchestrator:** pin runner image at startup ([#111](https://github.com/rumankazi/runnerforge/issues/111)) ([5525034](https://github.com/rumankazi/runnerforge/commit/55250343fb45ebe8d710d3df0a8be8e247cc27a4))

## [0.10.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.9.0...orchestrator-v0.10.0) (2026-06-10)


### Features

* **orchestrator:** poll VM creation outcomes via BackgroundTasks ([#108](https://github.com/rumankazi/runnerforge/issues/108)) ([9df1288](https://github.com/rumankazi/runnerforge/commit/9df1288070210abcaab23ce680f2ae76c20195d0))

## [0.9.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.8.0...orchestrator-v0.9.0) (2026-06-10)


### Features

* **orchestrator:** route VM egress through Cloud NAT ([#106](https://github.com/rumankazi/runnerforge/issues/106)) ([d306482](https://github.com/rumankazi/runnerforge/commit/d3064821505e9916f3830ea97b0c6567e8c6c1ff))

## [0.8.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.7.0...orchestrator-v0.8.0) (2026-06-10)


### Features

* **infra:** add Cloud NAT router + gateway for runner VM egress ([#99](https://github.com/rumankazi/runnerforge/issues/99)) ([4390cf7](https://github.com/rumankazi/runnerforge/commit/4390cf704874e1978c51906cd6d7599585ebb9ce))

## [0.7.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.6.1...orchestrator-v0.7.0) (2026-06-09)


### Features

* add opa policies and conftests ([#88](https://github.com/rumankazi/runnerforge/issues/88)) ([1f92904](https://github.com/rumankazi/runnerforge/commit/1f929046eeb2168dc3f9236d4c5dda5b42e84424))

## [0.6.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.6.0...orchestrator-v0.6.1) (2026-06-09)


### Bug Fixes

* renovate config ([#80](https://github.com/rumankazi/runnerforge/issues/80)) ([350f565](https://github.com/rumankazi/runnerforge/commit/350f565fcf489ac0ad88dc4bdbacd5b4516b2aa8))

## [0.6.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.5.1...orchestrator-v0.6.0) (2026-06-08)


### Features

* shared httpx + compute clients via lifespan-managed module globals ([#76](https://github.com/rumankazi/runnerforge/issues/76)) ([220a692](https://github.com/rumankazi/runnerforge/commit/220a692080aea93b005739788c5230718f349f15))

## [0.5.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.5.0...orchestrator-v0.5.1) (2026-06-08)


### Bug Fixes

* update the sweep schedule per day instead of every hour ([#73](https://github.com/rumankazi/runnerforge/issues/73)) ([9e96448](https://github.com/rumankazi/runnerforge/commit/9e96448a35c65c344427f7dd91a4b71c80470d19))

## [0.5.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.4.1...orchestrator-v0.5.0) (2026-06-08)


### Features

* **otel:** otel tracer wired up + tests ([#70](https://github.com/rumankazi/runnerforge/issues/70)) ([77acb3d](https://github.com/rumankazi/runnerforge/commit/77acb3dbc3154a7b48eee69efcd9ddcce83d52e1))

## [0.4.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.4.0...orchestrator-v0.4.1) (2026-06-07)


### Bug Fixes

* bug for startup script ([#67](https://github.com/rumankazi/runnerforge/issues/67)) ([e38a1ff](https://github.com/rumankazi/runnerforge/commit/e38a1ff5168e555598adb2f227c4121b977281d8))

## [0.4.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.3.3...orchestrator-v0.4.0) (2026-06-07)


### Features

* **orchestrator:** use runner-name explicitly ([#65](https://github.com/rumankazi/runnerforge/issues/65)) ([c830f75](https://github.com/rumankazi/runnerforge/commit/c830f7592d04849d07a9c6857351196b2b3e793c))

## [0.3.3](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.3.2...orchestrator-v0.3.3) (2026-06-07)


### Bug Fixes

* bug ofr boot disk name ([#63](https://github.com/rumankazi/runnerforge/issues/63)) ([75c18a6](https://github.com/rumankazi/runnerforge/commit/75c18a67888df2fd22d38ac4043c02a9dbf49a2f))

## [0.3.2](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.3.1...orchestrator-v0.3.2) (2026-06-07)


### Bug Fixes

* uniqueness of the runners ([#61](https://github.com/rumankazi/runnerforge/issues/61)) ([ffc99d6](https://github.com/rumankazi/runnerforge/commit/ffc99d63b4c4e5928cdb5d1866161ba34d7a90b3))

## [0.3.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.3.0...orchestrator-v0.3.1) (2026-06-07)


### Bug Fixes

* **orchestrator:** narrow SA permissions for runner vms ([#56](https://github.com/rumankazi/runnerforge/issues/56)) ([4f3a07f](https://github.com/rumankazi/runnerforge/commit/4f3a07f24574ad8e93390cb7a6af7ab82354ef9a))

## [0.3.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.2.1...orchestrator-v0.3.0) (2026-06-06)


### Features

* **ci:** deploy packer runner images workflow ([#52](https://github.com/rumankazi/runnerforge/issues/52)) ([2867b20](https://github.com/rumankazi/runnerforge/commit/2867b20b821559fc2fcaf7e59b7f0af81b1c3689))

## [0.3.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.2.1...orchestrator-v0.3.0) (2026-06-06)


### Features

* **ci:** deploy packer runner images workflow ([#52](https://github.com/rumankazi/runnerforge/issues/52)) ([2867b20](https://github.com/rumankazi/runnerforge/commit/2867b20b821559fc2fcaf7e59b7f0af81b1c3689))

## [0.2.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.2.0...orchestrator-v0.2.1) (2026-06-04)


### Bug Fixes

* skip fast for non-runnerforge labels ([#41](https://github.com/rumankazi/runnerforge/issues/41)) ([df6f962](https://github.com/rumankazi/runnerforge/commit/df6f96287ab9a7f072cdc7849975edb1b1351e23))

## [0.2.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.1.3...orchestrator-v0.2.0) (2026-06-02)


### Features

* **ci:** add release-please, conventional commits ([#31](https://github.com/rumankazi/runnerforge/issues/31)) ([50e58f2](https://github.com/rumankazi/runnerforge/commit/50e58f24e4a8c7846f1c3dc1b1b84840195e59c1))
