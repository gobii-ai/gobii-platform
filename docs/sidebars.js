const apiSidebar = require('./content/api-reference/sidebar.js');

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    {
      type: 'category',
      label: '👋 Getting Started',
      collapsed: false,
      items: ['getting-started/introduction'],
    },
    {
      type: 'category',
      label: '✨ Core Concepts',
      collapsed: false,
      items: [
        'core-concepts/agents',
        'core-concepts/agent-contacts',
        'core-concepts/files',
        'core-concepts/tasks',
        'core-concepts/task-credits',
      ],
    },
    {
      type: 'category',
      label: '🧭 Advanced Usage',
      collapsed: false,
      items: [
        'core-concepts/intelligence-selector',
        'core-concepts/organizations',
        'core-concepts/dedicated-ips',
        'advanced-usage/mcp-servers',
        'advanced-usage/custom-email-settings',
      ],
    },
    {
      type: 'category',
      label: '🖥️ Console Guides',
      collapsed: false,
      items: [
        'console-guides/overview',
        'console-guides/live-chat-guide',
        'console-guides/agent-settings',
        'console-guides/organizations-and-seats',
        'console-guides/billing-usage-and-tasks',
      ],
    },
    {
      type: 'category',
      label: '🛠️ Developers',
      collapsed: false,
      items: [
        'developers/developer-basics',
        'developers/developer-agents',
        'developers/developer-tasks',
        'developers/structured-data',
        'developers/webhooks',
      ],
    },
    {
      type: 'category',
      label: '🏠 Self-Hosted',
      collapsed: false,
      items: ['self-hosted/overview'],
    },
    {
      type: 'category',
      label: '⚙️ API Reference',
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
