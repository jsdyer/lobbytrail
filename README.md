# Lobby Trail

<!-- coolcode:guide -->
Select an organization, narrow its communication reports by year, institution or subject, and export the matching reports. Each report counts once even when several subjects are named; consultant registrations are outside this index.
<!-- /coolcode:guide -->

[Open Lobby Trail](https://coolcode.ca/lobbytrail/) to explore Canadian federal lobbying communication reports from corporation and organization in-house registrations. Search registry names and aliases, select an organization, and filter its reports by year, federally named institution or recorded subject. Inspect the evidence in the federal registry and export selected reports as CSV.

The [Office of the Commissioner of Lobbying](https://lobbycanada.gc.ca/en/open-data/) publishes a joined report archive. This build uses the primary report ID, registry organization ID, communication date, institution labels and controlled subject terms. It excludes superseded IDs where an amendment records a predecessor. Each report counts once even when it names multiple institutions or subjects. The site omits consultant registrations, personal names, position details, and free-text comments. A named institution or subject is an attribute of a filed report.

Run `python3 scripts/build.py` and serve `dist/` over HTTP. Pass `--input /path/to/communications.zip` to rebuild from a saved official download. Pass `--seed` to use the bundled snapshot explicitly. Python's standard library is sufficient. The organization directory loads once; the browser fetches just the compact report shard for a selected organization.

Every Tuesday GitHub Actions requests the official export and checks its schema and organization totals before saving a new snapshot on main. A blocked download, invalid archive or older source edition fails the refresh job and leaves the published data in place. Actions also supports a manual refresh. Pushes build and publish from the verified data already in the repository, so a code update works offline. The site reports the source export date, actual UTC retrieval date and communication date range separately.

The default local build tries the official download, and uses the last verified snapshot if the source is unavailable. `python3 scripts/build.py --refresh-seed` requires a successful online acquisition and updates `data/seed/` on a changed export. It can also be run from a machine with ordinary public access when the Registry denies a hosted runner's request. Commit the resulting manifest and its data archive together. Its SHA-256 is checked before the offline build copies its index and 64 filtered shards. Run `python3 -m unittest discover -s tests` to check the acquisition and validation paths.


Contains information licensed under the [Open Data Licence Agreement — Office of the Commissioner of Lobbying](https://lobbycanada.gc.ca/en/open-data/open-data-licence-agreement/). CoolCode is independent and unaffiliated with the Government of Canada.

<!-- schedule-reliability -->
## Schedule reliability

Public GitHub repositories can have scheduled workflows disabled after 60 days of inactivity. A separate weekly [schedule guard](.github/workflows/schedule-guard.yml) calls GitHub's enable-workflow API for an explicit list of refresh workflows and itself. It can restore workflows disabled for inactivity, preserves manually disabled workflows, and runs independently of collection failures.

This is an interim mitigation, not a scheduling guarantee: GitHub does not document active-workflow renewal as a permanent exemption, and scheduled runs can be delayed or dropped. A scheduler on our own Linux server is the planned independent trigger. The guard does not collect data, create dummy commits, or change source/check timestamps; successful collection and published data freshness still need monitoring.
<!-- /schedule-reliability -->
