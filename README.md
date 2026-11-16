# Lobby Trail

[Open Lobby Trail](https://coolcode.ca/lobbytrail/) to explore Canadian federal lobbying communication reports from corporation and organization in-house registrations. Search registry names and aliases, select an organization, and filter its reports by year, federally named institution or recorded subject. Inspect the evidence in the federal registry and export selected reports as CSV.

The [Office of the Commissioner of Lobbying](https://lobbycanada.gc.ca/en/open-data/) publishes a joined report archive. This build uses the primary report ID, registry organization ID, communication date, institution labels and controlled subject terms. It excludes superseded IDs where an amendment records a predecessor. Each report counts once even when it names multiple institutions or subjects. The site omits consultant registrations, personal names, position details, and free-text comments. A named institution or subject is an attribute of a filed report.

Run `python3 scripts/build.py` and serve `dist/` over HTTP. Pass `--input /path/to/communications.zip` to rebuild from a saved official download. Python's standard library is sufficient. The organization directory loads once; the browser fetches just the compact report shard for a selected organization. The app shows the report-export date from the source ZIP.

Contains information licensed under the [Open Data Licence Agreement — Office of the Commissioner of Lobbying](https://lobbycanada.gc.ca/en/open-data/open-data-licence-agreement/). CoolCode is independent and unaffiliated with the Government of Canada.
