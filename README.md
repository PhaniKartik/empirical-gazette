# The Empirical Gazette — Automated Edition

This version turns the Gazette into a self-updating daily science newspaper.

## What happens automatically

Every day a GitHub Actions workflow:

1. asks the OpenAI Responses API to research and draft one historical science dispatch;
2. checks the existing archive so it does not intentionally repeat a story;
3. finds a real historical image on Wikimedia Commons;
4. assigns the next dispatch number automatically;
5. updates the daily science quote;
6. updates “On This Day in Science”;
7. updates the featured scientific instrument;
8. adds “What They Got Wrong”, “What the Evidence Showed”, and “Primary Source”;
9. commits the updated data back to the repository.

GitHub Actions supports scheduled workflows using cron, and the workflow is also manually runnable from the Actions tab.

## One-time setup

1. Create a GitHub repository.
2. Upload this project to the repository.
3. In GitHub, open **Settings → Secrets and variables → Actions**.
4. Add a repository secret named `OPENAI_API_KEY`.
5. Enable GitHub Pages and publish the repository's root/main branch (or use another static host).
6. The workflow will run daily automatically.

The page itself is `index.html`.

## Important

The generated story is designed to be research-grounded, but automated publishing should still be treated as editorial automation rather than infallible fact-checking. The generator is explicitly instructed not to invent facts and to use primary-source information where available.

The image lookup deliberately uses Wikimedia Commons rather than generating a portrait, so the newspaper does not silently substitute an invented historical person.


## Editorial safety gate

Every edition follows **research → fact-check → historical image → article → validation → publish**. A separate validation pass must score at least 90/100. If validation fails, the previous edition is left untouched. Each successful dispatch stores its validation score, verified claims, research sources, and image source.

## Business-ready additions

This edition is deliberately structured for a free-first launch:

- provider-neutral email signup configuration in `site-config.js`;
- optional support, premium, and sponsor links that remain OFF by default;
- RSS feed generation for distribution and discovery;
- `robots.txt` and sitemap scaffold;
- optional analytics hook;
- a staged monetization plan in `BUSINESS_ROADMAP.md`.

The intended strategy is to build a durable reader relationship first, then introduce one monetization mechanism at a time without putting the core daily newspaper behind a paywall.
