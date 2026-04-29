const lightCodeTheme = require('prism-react-renderer').themes.github;
const darkCodeTheme = require('prism-react-renderer').themes.dracula;

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Gobii',
  tagline: 'Documentation for Gobii AI browser agents',
  favicon: 'images/favicon.png',
  url: process.env.DOCS_SITE_URL || 'https://docs.gobii.ai',
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

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        defaultMode: 'dark',
        respectPrefersColorScheme: true,
      },
      image: 'images/gobii-fish-with-text-dark-purple.png',
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
