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

The manifest includes detail pages plus their generated hire and spawn endpoints. Known routes redirect directly to the final canonical library URL. The seeded `/library/people/talent-sourcer/` collision is retired separately in the data migration and redirects to `/library/recruiting/candidate-sourcing-agent/`. Unlisted slugs return `404`.
