const apiSidebar = require('./content/api-reference/sidebar.js');

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Start Here',
      collapsed: false,
      link: {
        type: 'doc',
        id: 'getting-started/introduction',
      },
      items: [
        'start-here/what-is-gobii',
        'start-here/create-your-first-gobii',
        'start-here/core-concepts',
        'start-here/build-with-gobii',
      ],
    },
    {
      type: 'category',
      label: 'Using Gobii',
      link: {
        type: 'doc',
        id: 'using-gobii/index',
      },
      collapsed: false,
      items: [
        'using-gobii/chat-and-timeline',
        'using-gobii/planning-and-deliverables',
        'using-gobii/approvals-and-requests',
        'using-gobii/template-library',
        'using-gobii/share-template',
        'using-gobii/tools-and-apps',
        'using-gobii/connect-apps',
        'using-gobii/google-sheets',
        'using-gobii/mcp-servers',
        'using-gobii/channels-and-contacts',
        'using-gobii/email-and-sms',
        'using-gobii/files-and-workspaces',
        'using-gobii/secrets-and-credentials',
        'using-gobii/meta-gobii',
        'using-gobii/usage-credits-and-limits',
      ],
    },
    {
      type: 'category',
      label: 'Admin and Teams',
      link: {
        type: 'doc',
        id: 'admin-and-teams/index',
      },
      collapsed: false,
      items: [
        'admin-and-teams/organizations-and-seats',
        'admin-and-teams/collaborators-and-ownership',
        'admin-and-teams/contact-access-and-allowlists',
        'admin-and-teams/global-secrets',
        'admin-and-teams/organization-mcp-servers',
        'admin-and-teams/system-skills-and-profiles',
        'admin-and-teams/usage-and-billing',
        'admin-and-teams/dedicated-ips',
      ],
    },
    {
      type: 'category',
      label: 'Developers',
      link: {
        type: 'doc',
        id: 'developers/index',
      },
      collapsed: false,
      items: [
        'developers/developer-agents',
        'developers/mcp-server',
        'developers/webhooks',
        'developers/structured-data',
        'developers/developer-tasks',
        'developers/async-vs-sync',
      ],
    },
    {
      type: 'category',
      label: 'Self-Hosting',
      link: {
        type: 'doc',
        id: 'self-hosted/index',
      },
      collapsed: false,
      items: ['self-hosted/overview'],
    },
    {
      type: 'category',
      label: 'Engineering',
      link: {
        type: 'doc',
        id: 'engineering/index',
      },
      collapsed: false,
      items: [
        'engineering/evals',
      ],
    },
    {
      type: 'category',
      label: 'API Reference',
      link: {
        type: 'doc',
        id: 'api-reference/gobii-api',
      },
      collapsed: true,
      items: apiSidebar.apisidebar.slice(1),
    },
  ],
};

module.exports = sidebars;
