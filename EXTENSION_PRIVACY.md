# Personal Agent WaterMelon - browser extension privacy policy

*As of 5 November 2026.*

The Personal Agent WaterMelon browser extension (Chrome, Firefox) puts
**Delegate work** on the browser's right-click menu, and hands what was
right-clicked to the Personal Agent WaterMelon app on the same computer.

## What it reads, and when

- **When you pick Delegate work, and only that tab:** the page's address and
  title, the text you selected, the link or picture you right-clicked, and the
  page's visible text.
- **While you read a page (from version 0.0.3):** a moment after the page stops
  moving, the title of the tab in front and the words now inside its window.
  It uses this to tell what you are looking at, so that a recommendation fits.
  It needs a script in http and https pages. It does not read what you type in
  a field, and it keeps only the latest: each report replaces the one before.
- **Never in a private window.** The extension does not run there at all.

## What it tells the app by itself

- When the browser starts, and every six hours while it runs, it tells the app
  on the same computer that it is installed: which browser it is in, and
  whether it came from a store. Nothing about your pages or your browsing. The
  app's Settings use this to tell you when the extension was paused or
  removed.

## Where it goes

- To the **Personal Agent WaterMelon app on your own computer**, through the
  browser's native messaging - not over the network.
- **Nowhere else.** The extension sends nothing to its developers or to anyone:
  no analytics, no advertising, no tracking.
- What the app then does with it is the app's, on your computer, as you have
  set it up - including any AI model provider you chose for it.

## What it keeps

- In the browser's own storage for the extension, the local address of the
  app's page (for example `http://127.0.0.1:8756`), so the next right-click
  opens it at once. Nothing else.

## Questions

Open an issue at
<https://github.com/fhbzc/Personal-Agent-WaterMelon/issues>.
