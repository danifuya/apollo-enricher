# Apollo Enricher

CLI utilities for enriching Apollo company records with public Meta Ad Library data.

## Setup

Create `.env` from the example file:

```sh
cp .env.example .env
```

Then add the tokens you need:

```sh
APOLLO_API_KEY=<apollo_api_key>
META_ACCESS_TOKEN=<meta_access_token>
```

## Meta Review Commands

Resolve a public Facebook Page URL to a Page ID:

```sh
python3 scripts/apollo_cli.py meta-page-id "https://www.facebook.com/example-page/"
```

Query public Meta Ad Library data:

```sh
python3 scripts/apollo_cli.py meta-ads "example company" --country ES --active --sum-reach
```

## Notes

This tool does not publish content, manage Facebook Pages, read messages, or access private user data. It uses public page metadata to map known public Facebook Page URLs to Page IDs, then uses those Page IDs or search terms with the Meta Ad Library API.
