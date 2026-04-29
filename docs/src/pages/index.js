import React from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';

const primaryLinks = [
  {
    title: 'Start with Gobii',
    description: 'Understand Gobii agents, channels, organizations, files, and task credits.',
    to: '/getting-started/introduction',
  },
  {
    title: 'Build with the API',
    description: 'Create agents, submit browser-use tasks, receive webhooks, and retrieve structured results.',
    to: '/developers/developer-basics',
  },
  {
    title: 'Explore the API reference',
    description: 'Browse every REST endpoint with request parameters, response schemas, and code samples.',
    to: '/api-reference/list-persistent-agents',
  },
  {
    title: 'Self-host Gobii',
    description: 'Run Gobii on your own infrastructure with Docker Compose and local configuration.',
    to: '/self-hosted/overview',
  },
];

export default function Home() {
  return (
    <Layout
      title="Gobii Docs"
      description="Documentation for Gobii AI browser agents, browser-use task automation, API integrations, webhooks, MCP servers, and self-hosted deployments."
    >
      <main className="gobii-home">
        <section className="gobii-home__hero">
          <div>
            <p className="gobii-home__eyebrow">Gobii Documentation</p>
            <h1>AI browser agents that work across the web.</h1>
            <p className="gobii-home__lede">
              Learn how to create always-on agents, run browser-use tasks, integrate with the REST API, receive
              webhook results, and operate Gobii in the cloud or on your own infrastructure.
            </p>
            <div className="gobii-home__actions">
              <Link className="button button--primary" to="/getting-started/introduction">
                Read the docs
              </Link>
              <Link className="button button--secondary" to="/api-reference/list-persistent-agents">
                API reference
              </Link>
            </div>
          </div>
        </section>
        <section className="gobii-home__links" aria-label="Documentation sections">
          {primaryLinks.map((item) => (
            <Link className="gobii-home__link" to={item.to} key={item.to}>
              <span>{item.title}</span>
              <p>{item.description}</p>
            </Link>
          ))}
        </section>
      </main>
    </Layout>
  );
}
