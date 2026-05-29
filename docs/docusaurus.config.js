const lightCodeTheme = require('prism-react-renderer').themes.github;
const darkCodeTheme = require('prism-react-renderer').themes.dracula;

const siteUrl = process.env.DOCS_SITE_URL || 'https://docs.gobii.ai';
const socialImage = `${siteUrl}/images/gobii-fish-with-text-dark-purple.png`;
const gtagTrackingId = process.env.DOCS_GTAG_TRACKING_ID;

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Gobii',
  tagline: 'Documentation for Gobii AI employees, teams, integrations, and developer APIs',
  favicon: 'favicon.ico',
  url: siteUrl,
  baseUrl: '/',
  organizationName: 'gobii-ai',
  projectName: 'gobii-platform',
  trailingSlash: false,
  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  plugins: [
    [
      'docusaurus-plugin-openapi-docs',
      {
        id: 'openapi',
        docsPluginId: 'classic',
        config: {
          gobii: {
            specPath: 'static/openapi/GobiiAPI.yaml',
            outputDir: 'content/api-reference',
            sidebarOptions: {
              groupPathsBy: 'tag',
              categoryLinkSource: 'tag',
              sidebarCollapsed: false,
            },
          },
        },
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          path: 'content',
          routeBasePath: '/',
          sidebarPath: require.resolve('./sidebars.js'),
          docItemComponent: '@theme/ApiItem',
          editUrl: 'https://github.com/gobii-ai/gobii-platform/tree/main/docs/',
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
        ...(gtagTrackingId
          ? {
              gtag: {
                trackingID: gtagTrackingId,
                anonymizeIP: true,
              },
            }
          : {}),
        sitemap: {
          changefreq: 'weekly',
          priority: 0.7,
          ignorePatterns: ['/tags/**'],
          filename: 'sitemap.xml',
        },
      },
    ],
  ],

  themes: ['docusaurus-theme-openapi-docs'],
  headTags: [
    {
      tagName: 'meta',
      attributes: {
        name: 'theme-color',
        content: '#090b0f',
      },
    },
    {
      tagName: 'script',
      attributes: {
        type: 'application/ld+json',
      },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'Organization',
        '@id': 'https://gobii.ai/#organization',
        name: 'Gobii',
        url: 'https://gobii.ai',
        logo: `${siteUrl}/images/favicon.png`,
        sameAs: [
          'https://github.com/gobii-ai',
          'https://www.linkedin.com/company/gobii-ai',
          'https://x.com/gobii_ai',
        ],
      }),
    },
    {
      tagName: 'script',
      attributes: {
        type: 'application/ld+json',
      },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        name: 'Gobii Docs',
        url: siteUrl,
        publisher: {
          '@id': 'https://gobii.ai/#organization',
        },
      }),
    },
    {
      tagName: 'script',
      attributes: {
        type: 'application/ld+json',
      },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'SoftwareSourceCode',
        '@id': 'https://github.com/gobii-ai/gobii-platform#source-code',
        name: 'Gobii Platform',
        codeRepository: 'https://github.com/gobii-ai/gobii-platform',
        url: siteUrl,
        license: 'https://github.com/gobii-ai/gobii-platform/blob/main/LICENSE',
        publisher: {
          '@id': 'https://gobii.ai/#organization',
        },
      }),
    },
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      metadata: [
        {
          name: 'description',
          content:
            'Gobii documentation for creating and managing AI employees, using templates, channels, files, approvals, tools, teams, APIs, Remote MCP, webhooks, and self-hosted deployments.',
        },
        {name: 'keywords', content: 'Gobii, AI employees, Gobiis, AI agents, automation, Remote MCP, webhooks, teams, templates, self-hosted'},
        {property: 'og:site_name', content: 'Gobii Docs'},
        {property: 'og:type', content: 'website'},
      ],
      colorMode: {
        defaultMode: 'dark',
        respectPrefersColorScheme: true,
      },
      image: socialImage,
      navbar: {
        logo: {
          alt: 'Gobii',
          src: 'images/gobii-fish-with-text-dark-purple.png',
          srcDark: 'images/gobii-fish-with-text-light-purple.png',
          href: 'https://gobii.ai',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docsSidebar',
            position: 'left',
            label: 'Documentation',
          },
          {
            to: '/api-reference/agents-api',
            label: 'API Reference',
            position: 'left',
          },
          {
            href: 'https://github.com/gobii-ai/gobii-platform',
            label: 'GitHub',
            position: 'right',
          },
          {
            href: 'https://gobii.ai/accounts/signup/',
            label: 'Sign up',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Gobii',
            items: [
              { label: 'Console', href: 'https://gobii.ai/console/' },
              { label: 'Sign up', href: 'https://gobii.ai/accounts/signup/' },
              { label: 'GitHub', href: 'https://github.com/gobii-ai/gobii-platform' },
            ],
          },
          {
            title: 'Docs',
            items: [
              { label: 'Start here', to: '/' },
              { label: 'Using Gobii', to: '/using-gobii' },
              { label: 'Admin and teams', to: '/admin-and-teams' },
              { label: 'Developer basics', to: '/developers/developer-basics' },
              { label: 'API reference', to: '/api-reference/agents-api' },
              { label: 'Self-hosting', to: '/self-hosted' },
            ],
          },
        ],
        copyright: `Copyright ${new Date().getFullYear()} Gobii, Inc.`,
      },
      prism: {
        theme: lightCodeTheme,
        darkTheme: darkCodeTheme,
        additionalLanguages: ['bash', 'json', 'python', 'javascript', 'typescript'],
      },
      docs: {
        sidebar: {
          hideable: true,
          autoCollapseCategories: false,
        },
      },
    }),
};

module.exports = config;
