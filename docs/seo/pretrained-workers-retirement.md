# `/pretrained-workers/` retirement inventory

The redirect manifest is [`pretrained-workers-redirect-manifest.csv`](pretrained-workers-redirect-manifest.csv).

The inventory was assembled from:

- Django route definitions for the directory, detail, hire, and spawn endpoints.
- `PretrainedWorkerTemplateService` fallback definitions and its `talent-sourcer` alias.
- Active database-backed templates without an organization or public-profile owner.
- Homepage cards and all solution-page detail and creation links.
- Public-template detail pages and their related-template links, breadcrumbs, structured data, Open Graph metadata, and canonical URL helpers.
- Static, category, template, solution, and blog sitemap generation.
- Documentation, proprietary blog content, tests, migrations, and seed data.
- Existing `PersistentAgentTemplateUrlAlias` records and redirect views.

No Search Console export or server/access-log export exists in this repository. A read-only production database inventory was available and was included. The cutover snapshot found 127 active organization-less templates whose internal codes resolved through the old catch-all view. The manifest retains permanent one-to-one redirects for all of them, including 112 public-template `tpl-*` codes. Template codes introduced after the cutover are unknown legacy slugs and return `404`.

The manifest includes detail pages plus their generated hire and spawn endpoints. Both slashless and trailing-slash forms of known routes redirect directly to the final canonical library URL and preserve query parameters. The seeded `/library/people/talent-sourcer/` collision is retired separately in the data migration and redirects to `/library/recruiting/candidate-sourcing-agent/`. Unlisted slugs return `404`.

## Collision decisions

- **Talent Sourcer and Talent Scout:** Candidate Sourcing is the canonical destination. Its content and tool guidance now preserve discovery and qualification, human-reviewed outreach drafting, candidate and response tracking, weekly funnel reporting, Greenhouse candidate creation, Google Sheets tracking, and Slack notifications.
- **Lead Hunter:** The historical Gobii Lead Hunter route remains consolidated into the official B2B Lead Research page. The separate community Lead Hunter page is retained because it has an independent owner and demonstrated usage. Its heading, introduction, and metadata now position it specifically around individual prospect discovery on professional networks, rather than the official template's broader company research, account-fit, buying-signal, and source-reasoning workflow.
- **Capital Raise & Investor Relations Engine:** The two pages had the same owner, no likes, and no attributed signups. The richer copy from the newer `-2` record is merged into the original URL, the duplicate is deactivated, and its Library and legacy-code routes permanently redirect to the original page.
- **Renewable Energy Market Analyst:** Both pages remain because they have different owners and distinct workflows. One is positioned for daily news monitoring; the other is positioned for analytical trend reports with data visualizations.
- **Versatile Research & Data Assistant:** Both pages remain because they have different owners and distinct workflows. One is positioned for web research and data analysis; the other is positioned for conversation-led file processing and visualization.
