const lightCodeTheme = require('prism-react-renderer').themes.github;
const darkCodeTheme = require('prism-react-renderer').themes.dracula;

const siteUrl = process.env.DOCS_SITE_URL || 'https://docs.gobii.ai';
const socialImage = `${siteUrl}/images/gobii-fish-with-text-dark-purple.png`;

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Gobii',
  tagline: 'Documentation for Gobii AI browser agents',
  favicon: 'images/favicon.png',
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
        name: 'Gobii',
        url: 'https://gobii.ai',
        logo: `${siteUrl}/images/favicon.png`,
        sameAs: ['https://github.com/gobii-ai/gobii-platform'],
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
          '@type': 'Organization',
          name: 'Gobii',
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
            'Gobii documentation for AI browser agents, browser-use task automation, API integrations, webhooks, MCP servers, and self-hosted deployments.',
        },
        {name: 'keywords', content: 'Gobii, AI browser agents, browser-use, browser automation API, AI agents, web automation'},
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
            to: '/api-reference/list-persistent-agents',
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
              { label: 'Getting started', to: '/getting-started/introduction' },
              { label: 'Developer basics', to: '/developers/developer-basics' },
              { label: 'API reference', to: '/api-reference/list-persistent-agents' },
              { label: 'Self-hosted', to: '/self-hosted/overview' },
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
