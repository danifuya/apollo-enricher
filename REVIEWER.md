# Meta Reviewer Instructions

This repository demonstrates why the app requests Page Public Metadata Access.

## Use Case

We store public Facebook Page URLs for companies in our CRM. We need to resolve those public URLs to public Meta Page IDs. Those Page IDs are then used with the Meta Ad Library API `search_page_ids` parameter to retrieve public ad transparency data for the correct company page.

We only need public page metadata:

```text
id
name
link
username
```

We do not manage pages, publish content, read messages, access private data, or modify any Facebook Page.

## Commands

Install no dependencies. The CLI uses Python standard library only.

Create `.env`:

```sh
cp .env.example .env
```

Add a Meta token:

```sh
META_ACCESS_TOKEN=<token>
```

Run the Page ID resolution command:

```sh
python3 scripts/apollo_cli.py meta-page-id "https://www.facebook.com/example-page/"
```

Expected result after Page Public Metadata Access is approved:

```text
Page ID: ...
Name: ...
Username: ...
Link: ...
```

Current result without Page Public Metadata Access:

```text
Meta API returned HTTP 400: This endpoint requires the 'pages_read_engagement' permission or the 'Page Public Content Access' feature or the 'Page Public Metadata Access' feature.
```

Run the existing Ad Library flow:

```sh
python3 scripts/apollo_cli.py meta-ads "example company" --country ES --active --sum-reach
```

This queries public Meta Ad Library data and prints public ad transparency information such as active ad count, EU reach, and publisher platforms.
