# Changelog

## [0.30.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.29.0...orchestrator-v0.30.0) (2026-11-13)


### Features

* **orchestrator:** slot ledger in shadow mode ([#408](https://github.com/rumankazi/runnerforge/issues/408)) ([aabe3d6](https://github.com/rumankazi/runnerforge/commit/aabe3d635b202de4a9b292cd41bd7e4e0786e808))


### Bug Fixes

* **deps:** lock file maintenance ([#411](https://github.com/rumankazi/runnerforge/issues/411)) ([7acd5df](https://github.com/rumankazi/runnerforge/commit/7acd5dfee28cb2edde5ca8cfd725879da5f4230f))
* **deps:** update uv to v0.13.8 ([#415](https://github.com/rumankazi/runnerforge/issues/415)) ([2e30e29](https://github.com/rumankazi/runnerforge/commit/2e30e292c853be8595b8090d1825d4d225c8e7aa))

## [0.29.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.28.1...orchestrator-v0.29.0) (2026-11-08)


### Features

* **orchestrator:** read tenant limits written by other versions ([#402](https://github.com/rumankazi/runnerforge/issues/402)) ([82325e3](https://github.com/rumankazi/runnerforge/commit/82325e35fb190420d92863329577826a936c7891))

## [0.28.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.28.0...orchestrator-v0.28.1) (2026-11-07)


### Bug Fixes

* **orchestrator:** count a VM busy for serial logging when GitHub says its runner is ([#397](https://github.com/rumankazi/runnerforge/issues/397)) ([496209a](https://github.com/rumankazi/runnerforge/commit/496209a34fc448859b8061ed5ad41b586d1b67ae))

## [0.28.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.27.0...orchestrator-v0.28.0) (2026-11-06)


### Features

* **orchestrator:** run VMs in a configurable project and turn serial logging off when a job starts ([110591d](https://github.com/rumankazi/runnerforge/commit/110591de419ca49a7c848d118b8a251da886607b))


### Bug Fixes

* **deps:** update uv to v0.13.7 ([#381](https://github.com/rumankazi/runnerforge/issues/381)) ([fc0ed69](https://github.com/rumankazi/runnerforge/commit/fc0ed694b70593fd77f63803ed3d17bb01fb00f9))

## [0.27.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.26.1...orchestrator-v0.27.0) (2026-11-02)


### Features

* **orchestrator:** clean up after installations GitHub cuts off ([#370](https://github.com/rumankazi/runnerforge/issues/370)) ([225ac3b](https://github.com/rumankazi/runnerforge/commit/225ac3bb6ca30c9f244e493780a3401d8b5ca264))

## [0.26.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.26.0...orchestrator-v0.26.1) (2026-11-02)


### Bug Fixes

* **deps:** lock file maintenance ([#359](https://github.com/rumankazi/runnerforge/issues/359)) ([b499706](https://github.com/rumankazi/runnerforge/commit/b49970632a5283e7557405d4a994fc75abf49877))

## [0.26.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.25.0...orchestrator-v0.26.0) (2026-11-01)


### Features

* **orchestrator:** reconciler deletes orphan VMs ([#355](https://github.com/rumankazi/runnerforge/issues/355)) ([26f0fbe](https://github.com/rumankazi/runnerforge/commit/26f0fbe5943861d416faa9e9d602f4e31548bf3a))

## [0.25.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.24.2...orchestrator-v0.25.0) (2026-11-01)


### Features

* **orchestrator:** reconciler starts deferred jobs as capacity frees ([#351](https://github.com/rumankazi/runnerforge/issues/351)) ([5558db9](https://github.com/rumankazi/runnerforge/commit/5558db958dcf7935b0ae609c55be9fa9a5c526a5))

## [0.24.2](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.24.1...orchestrator-v0.24.2) (2026-11-01)


### Bug Fixes

* **orchestrator:** keep the VM watcher polling through connection errors ([#347](https://github.com/rumankazi/runnerforge/issues/347)) ([4a2d7d8](https://github.com/rumankazi/runnerforge/commit/4a2d7d822d86f3a65d90e39baaad7469f71973a0))

## [0.24.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.24.0...orchestrator-v0.24.1) (2026-11-01)


### Bug Fixes

* **orchestrator:** skip readiness samples when GitHub's clock disagrees ([#345](https://github.com/rumankazi/runnerforge/issues/345)) ([1ff20d9](https://github.com/rumankazi/runnerforge/commit/1ff20d91282f6750eb9efe4cdfc5d0a0cc1a6a5b))

## [0.24.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.23.0...orchestrator-v0.24.0) (2026-11-01)


### Features

* **orchestrator:** registry admission and tenant limits behind ADMISSION ([#344](https://github.com/rumankazi/runnerforge/issues/344)) ([f4ddecc](https://github.com/rumankazi/runnerforge/commit/f4ddecce726afd4b2cf75eec8505a4d9ce554b04))
* **orchestrator:** runnerforge-admin command for the tenant registry ([#342](https://github.com/rumankazi/runnerforge/issues/342)) ([4a1dea9](https://github.com/rumankazi/runnerforge/commit/4a1dea939d677959e4a0b6f6d35558e084127f7a))

## [0.23.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.22.0...orchestrator-v0.23.0) (2026-10-30)


### Features

* **orchestrator:** tenant registry from installation events, in shadow mode ([#337](https://github.com/rumankazi/runnerforge/issues/337)) ([fe8614c](https://github.com/rumankazi/runnerforge/commit/fe8614c797d8daf0274e695b21a41f0885b992aa))

## [0.22.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.21.0...orchestrator-v0.22.0) (2026-10-30)


### Features

* **orchestrator:** reconcile job records with GitHub and GCE ([#333](https://github.com/rumankazi/runnerforge/issues/333)) ([69e2ee7](https://github.com/rumankazi/runnerforge/commit/69e2ee7cfac881cf542d04451759db2fce8d5b4c))

## [0.21.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.20.0...orchestrator-v0.21.0) (2026-10-30)


### Features

* **orchestrator:** write per-job records at every lifecycle point ([#328](https://github.com/rumankazi/runnerforge/issues/328)) ([4669b28](https://github.com/rumankazi/runnerforge/commit/4669b28e5874fb1a8891b4a2d0e54fd1532ec093))

## [0.20.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.19.0...orchestrator-v0.20.0) (2026-10-30)


### Features

* **orchestrator:** add the per-job store, inert until JOB_STORE=firestore ([#325](https://github.com/rumankazi/runnerforge/issues/325)) ([1e0491c](https://github.com/rumankazi/runnerforge/commit/1e0491c96edfdf17a448499d6b207ca8571b8cee))

## [0.19.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.18.0...orchestrator-v0.19.0) (2026-10-28)


### Features

* **orchestrator:** log one outcome per queued webhook ([#308](https://github.com/rumankazi/runnerforge/issues/308)) ([a4624a4](https://github.com/rumankazi/runnerforge/commit/a4624a4b335cc1877b017c72f3f0db9f51611fbc))


### Bug Fixes

* **orchestrator:** bound the gone-VM preemption cross-check ([#312](https://github.com/rumankazi/runnerforge/issues/312)) ([ed01108](https://github.com/rumankazi/runnerforge/commit/ed0110865fc98ab58f440344e3849e876b0ca904))
* **orchestrator:** run preemption cross-check when the VM is already gone ([#306](https://github.com/rumankazi/runnerforge/issues/306)) ([2ebaedc](https://github.com/rumankazi/runnerforge/commit/2ebaedc9c49c402341dd65ce71a439149b0c31f4))
* **orchestrator:** sweep decides on the VM's runner, not the job it was booted for ([#307](https://github.com/rumankazi/runnerforge/issues/307)) ([bf82f11](https://github.com/rumankazi/runnerforge/commit/bf82f1136c931dca1e64ca68640a77201ab518c2))

## [0.18.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.8...orchestrator-v0.18.0) (2026-10-27)


### Features

* **orchestrator:** installation allow-list for VM creation ([#303](https://github.com/rumankazi/runnerforge/issues/303)) ([5934ce3](https://github.com/rumankazi/runnerforge/commit/5934ce3086171762aba632b1b3bf6be7925b26cb))

## [0.17.8](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.7...orchestrator-v0.17.8) (2026-10-27)


### Bug Fixes

* **orchestrator:** cap VM runtime, label disks, decouple image and op project ([#300](https://github.com/rumankazi/runnerforge/issues/300)) ([cdfdf01](https://github.com/rumankazi/runnerforge/commit/cdfdf01bc121222d7381bba7671849f394df97cf))

## [0.17.7](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.6...orchestrator-v0.17.7) (2026-10-27)


### Bug Fixes

* **orchestrator:** dispatch webhooks on X-GitHub-Event ([#298](https://github.com/rumankazi/runnerforge/issues/298)) ([6669c2c](https://github.com/rumankazi/runnerforge/commit/6669c2c2c84a731e0c490fa4d55a7fccf3b96ea2))

## [0.17.6](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.5...orchestrator-v0.17.6) (2026-10-27)


### Bug Fixes

* **orchestrator:** accept GitHub default runner labels case-insensitively ([#296](https://github.com/rumankazi/runnerforge/issues/296)) ([f0b50e2](https://github.com/rumankazi/runnerforge/commit/f0b50e2cb1e03d861315e366e2310d3022bd5c88))

## [0.17.5](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.4...orchestrator-v0.17.5) (2026-10-27)


### Bug Fixes

* **orchestrator:** drop legacy repo label and harden sweep/label lookups ([#294](https://github.com/rumankazi/runnerforge/issues/294)) ([19f93db](https://github.com/rumankazi/runnerforge/commit/19f93db6929f9dc3b4a8638416bfcdb042618691))

## [0.17.4](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.3...orchestrator-v0.17.4) (2026-10-27)


### Bug Fixes

* **orchestrator:** sweep reads exact repo from VM metadata ([#293](https://github.com/rumankazi/runnerforge/issues/293)) ([eadb30c](https://github.com/rumankazi/runnerforge/commit/eadb30ce1612a08e878ffc288c6170462857ba61))
* runnerforge owned vms are deleted ([#291](https://github.com/rumankazi/runnerforge/issues/291)) ([4d4a3f3](https://github.com/rumankazi/runnerforge/commit/4d4a3f3e545a5b5a6ba858984d9fb9712d69aa49))

## [0.17.3](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.2...orchestrator-v0.17.3) (2026-10-27)


### Bug Fixes

* **deps:** update uv to v0.13.6 ([#288](https://github.com/rumankazi/runnerforge/issues/288)) ([75214bb](https://github.com/rumankazi/runnerforge/commit/75214bb31630a9f0d60f7e6be6a05f38ad83253c))

## [0.17.2](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.1...orchestrator-v0.17.2) (2026-10-27)


### Bug Fixes

* **deps:** update dependency python to v3.15.2 ([#281](https://github.com/rumankazi/runnerforge/issues/281)) ([9b2507c](https://github.com/rumankazi/runnerforge/commit/9b2507c765398989a132883b57b580136edaa827))
* **deps:** update dependency python to v3.15.2 ([#286](https://github.com/rumankazi/runnerforge/issues/286)) ([d59841a](https://github.com/rumankazi/runnerforge/commit/d59841acc33bd03b4453f679283fe1833b8eba5e))

## [0.17.1](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.17.0...orchestrator-v0.17.1) (2026-10-26)


### Bug Fixes

* **deps:** lock file maintenance ([#280](https://github.com/rumankazi/runnerforge/issues/280)) ([f954ebd](https://github.com/rumankazi/runnerforge/commit/f954ebd0d16cac66771df40d72d19e249a5dffc7))

## [0.17.0](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.6...orchestrator-v0.17.0) (2026-10-24)


### Features

* **deps:** update orchestrator deps (minor) ([#267](https://github.com/rumankazi/runnerforge/issues/267)) ([9f7784a](https://github.com/rumankazi/runnerforge/commit/9f7784a6f464679195df105b31b9f22d9dfcfc13))
* **deps:** update uv to v0.13.5 ([#268](https://github.com/rumankazi/runnerforge/issues/268)) ([1a732e2](https://github.com/rumankazi/runnerforge/commit/1a732e2a62e9f74df94359adeb4ae238412a547e))


### Bug Fixes

* **deps:** bump pyjwt, urllib3 and certifi in orchestrator ([#270](https://github.com/rumankazi/runnerforge/issues/270)) ([71094cd](https://github.com/rumankazi/runnerforge/commit/71094cdc3fc70741484a8e275328c1f2ff0d1f5a))
* **deps:** lock file maintenance ([#221](https://github.com/rumankazi/runnerforge/issues/221)) ([50dd3e4](https://github.com/rumankazi/runnerforge/commit/50dd3e4cf477dfa811b39777544474bd391aaf15))

## [0.16.6](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.5...orchestrator-v0.16.6) (2026-08-23)


### Bug Fixes

* **deps:** lock file maintenance ([#210](https://github.com/rumankazi/runnerforge/issues/210)) ([e8ec4fe](https://github.com/rumankazi/runnerforge/commit/e8ec4fe638957924882a9556df01b25117157a7b))

## [0.16.5](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.4...orchestrator-v0.16.5) (2026-08-15)


### Bug Fixes

* **deps:** lock file maintenance ([#186](https://github.com/rumankazi/runnerforge/issues/186)) ([917838e](https://github.com/rumankazi/runnerforge/commit/917838e28093ad6e316f618d5b9c549bc074275a))
* **deps:** lock file maintenance ([#199](https://github.com/rumankazi/runnerforge/issues/199)) ([64eff3d](https://github.com/rumankazi/runnerforge/commit/64eff3d35c13745744883d4d15316e031722e72d))

## [0.16.4](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.3...orchestrator-v0.16.4) (2026-08-09)


### Bug Fixes

* **deps:** lock file maintenance ([#170](https://github.com/rumankazi/runnerforge/issues/170)) ([56bf5ce](https://github.com/rumankazi/runnerforge/commit/56bf5ce8a1fff1489fa26db784e6aa4744318495))
* **deps:** lock file maintenance ([#172](https://github.com/rumankazi/runnerforge/issues/172)) ([debbf66](https://github.com/rumankazi/runnerforge/commit/debbf66b2d563d7f379e2ea7d44ae239da279ae8))
* **deps:** lock file maintenance ([#176](https://github.com/rumankazi/runnerforge/issues/176)) ([7bedc8c](https://github.com/rumankazi/runnerforge/commit/7bedc8c00249c7792bfad6327ea6d48cbd7e270a))
* **deps:** lock file maintenance ([#181](https://github.com/rumankazi/runnerforge/issues/181)) ([3c353a6](https://github.com/rumankazi/runnerforge/commit/3c353a6a5b8f0607f3500b84b8f0113cfdf1c26b))
* **orchestrator:** unblock CI — ruff 0.16 rule set and base-image CVEs ([#191](https://github.com/rumankazi/runnerforge/issues/191)) ([2f614de](https://github.com/rumankazi/runnerforge/commit/2f614de1021fe727d11d3aa1a71470457a43f5d7))

## [0.16.3](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.2...orchestrator-v0.16.3) (2026-06-15)


### Bug Fixes

* **deps:** lock file maintenance ([#160](https://github.com/rumankazi/runnerforge/issues/160)) ([9b4ff07](https://github.com/rumankazi/runnerforge/commit/9b4ff07f51ae54d2cac50754690d0d0d89916a61))

## [0.16.2](https://github.com/rumankazi/runnerforge/compare/orchestrator-v0.16.1...orchestrator-v0.16.2) (2026-06-14)


### Bug Fixes

* **deps:** lock file maintenance ([#156](https://github.com/rumankazi/runnerforge/issues/156)) ([e8f51d6](https://github.com/rumankazi/runnerforge/commit/e8f51d66e95b06a171aa7e8df8d84aa12879b92a))

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
