# SFMOMA feeds

Self-built RSS feeds for SFMOMA press releases and Stories articles, scraped
three times a day by GitHub Actions and served from GitHub Pages.

| Feed | URL |
|---|---|
| Press releases | `https://YOUR-USERNAME.github.io/sfmoma-feed/press.xml` |
| Stories | `https://YOUR-USERNAME.github.io/sfmoma-feed/stories.xml` |
| Both combined | `https://YOUR-USERNAME.github.io/sfmoma-feed/all.xml` |

## Setup

1. Create a **public** repo named `sfmoma-feed` (Pages on a free account needs a public repo) and push these files.
2. In `sfmoma_feed.py`, set `FEED_BASE_URL` to your Pages URL and put a real contact in `USER_AGENT`.
3. Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/docs`.
4. Actions tab → *Build SFMOMA feeds* → *Run workflow* to do the first build.
5. Subscribe to the feed URLs above in your reader.

## Running locally

    pip install -r requirements.txt
    python sfmoma_feed.py

## How it works

- Scrapes the listing pages `sfmoma.org/press/release/` and `sfmoma.org/read/`.
- Each new article page is fetched once for its title, summary, image and date; results are cached in `state.json`.
- Press release dates come from the listing. Story dates come from the page's `article:published_time` if present, otherwise the date the scraper first saw the item.
- Feed files are only rewritten (and committed) when their contents change.

## When it breaks

If SFMOMA redesigns the site, the run logs a warning such as
`stories: no items found` and the job shows as failed. The fix is usually
the `path_pattern` or `listing` URL in the `SOURCES` dict at the top of the script.

GitHub pauses scheduled workflows in public repos after 60 days with no repo
activity. If the feeds go quiet for that long, re-enable the workflow from the Actions tab.
